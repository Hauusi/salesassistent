"""Product-suggestion review workflow: a detected article number + text
from an incoming mail (see app/services/product_suggestion_service.py)
never enters the real catalog on its own - a human approves or rejects it
here, mirroring the Draft approval gate in app/api/routes/drafts.py.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_tenant, get_current_user
from app.db import get_db
from app.models.enums import ActionActor, ProductSuggestionStatus
from app.models.product import Product
from app.models.product_suggestion import ProductSuggestion
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.product_suggestion import ProductSuggestionOut, ProductSuggestionRejectIn
from app.services.action_log_service import log_action

router = APIRouter(prefix="/api/product-suggestions", tags=["product-suggestions"])


async def _get_suggestion_or_404(
    db: AsyncSession, tenant: Tenant, suggestion_id: uuid.UUID, *, for_update: bool = False
) -> ProductSuggestion:
    stmt = select(ProductSuggestion).where(
        ProductSuggestion.id == suggestion_id, ProductSuggestion.tenant_id == tenant.id
    )
    if for_update:
        # Same reasoning as Draft approval (app/api/routes/drafts.py):
        # serialises two concurrent approve/reject calls on the same
        # suggestion, so the loser sees a status that has already moved on
        # instead of both creating a Product for it.
        stmt = stmt.with_for_update()
    stmt = stmt.options(selectinload(ProductSuggestion.email_message))

    suggestion = (await db.execute(stmt)).scalar_one_or_none()
    if suggestion is None:
        raise HTTPException(status_code=404, detail="Produktvorschlag nicht gefunden.")
    return suggestion


@router.get("", response_model=list[ProductSuggestionOut])
async def list_product_suggestions(
    status: ProductSuggestionStatus | None = None,
    db: AsyncSession = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> list[ProductSuggestion]:
    stmt = (
        select(ProductSuggestion)
        .where(ProductSuggestion.tenant_id == tenant.id)
        .options(selectinload(ProductSuggestion.email_message))
        .order_by(ProductSuggestion.created_at.desc())
    )
    stmt = (
        stmt.where(ProductSuggestion.status == status)
        if status
        else stmt.where(ProductSuggestion.status == ProductSuggestionStatus.VORGESCHLAGEN)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.post("/{suggestion_id}/approve", response_model=ProductSuggestionOut)
async def approve_product_suggestion(
    suggestion_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
    user: User = Depends(get_current_user),
) -> ProductSuggestion:
    """Creates the real catalog Product from a pending suggestion.

    Unlike Draft approval there is no external side effect to protect (no
    mail gets sent) - the row lock only has to stop two concurrent
    approvals from both creating a Product for the same suggestion.
    """
    suggestion = await _get_suggestion_or_404(db, tenant, suggestion_id, for_update=True)
    if suggestion.status != ProductSuggestionStatus.VORGESCHLAGEN:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Vorschlag ist im Status '{suggestion.status.value}' - "
                "nur 'vorgeschlagen' kann freigegeben werden."
            ),
        )

    existing = (
        await db.execute(
            select(Product.id).where(Product.tenant_id == tenant.id, Product.sku == suggestion.sku)
        )
    ).scalar_one_or_none()
    if existing is not None:
        # The SKU could have entered the catalog by another route (a second
        # suggestion approved first, a manual add) between this suggestion
        # being created and now - the catalog is the source of truth for
        # uniqueness, so refuse rather than violate uq_product_tenant_sku.
        raise HTTPException(
            status_code=409,
            detail=f"Artikelnummer '{suggestion.sku}' existiert bereits im Produktkatalog.",
        )

    product = Product(
        tenant_id=tenant.id,
        name=suggestion.name,
        description=suggestion.description,
        sku=suggestion.sku,
    )
    db.add(product)
    await db.flush()

    suggestion.status = ProductSuggestionStatus.FREIGEGEBEN
    suggestion.reviewed_by_user_id = user.id
    suggestion.reviewed_at = datetime.now(UTC)
    suggestion.created_product_id = product.id

    await log_action(
        db,
        tenant_id=tenant.id,
        actor=ActionActor.USER,
        actor_user_id=user.id,
        entity_type="product_suggestion",
        entity_id=suggestion.id,
        action="product_suggestion_approved",
        detail={"sku": suggestion.sku, "product_id": str(product.id)},
    )
    await db.commit()
    # Reload through the same eager options rather than db.refresh(): see
    # app/api/routes/drafts.py for why (refresh() expires email_message,
    # and the response model serialises it - a lazy load inside an async
    # request raises MissingGreenlet).
    return await _get_suggestion_or_404(db, tenant, suggestion.id)


@router.post("/{suggestion_id}/reject", response_model=ProductSuggestionOut)
async def reject_product_suggestion(
    suggestion_id: uuid.UUID,
    payload: ProductSuggestionRejectIn,
    db: AsyncSession = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
    user: User = Depends(get_current_user),
) -> ProductSuggestion:
    suggestion = await _get_suggestion_or_404(db, tenant, suggestion_id)
    if suggestion.status != ProductSuggestionStatus.VORGESCHLAGEN:
        raise HTTPException(
            status_code=409,
            detail="Nur Vorschläge im Status 'vorgeschlagen' können abgelehnt werden.",
        )

    suggestion.status = ProductSuggestionStatus.ABGELEHNT
    suggestion.reviewed_by_user_id = user.id
    suggestion.reviewed_at = datetime.now(UTC)
    suggestion.rejected_reason = payload.reason

    await log_action(
        db,
        tenant_id=tenant.id,
        actor=ActionActor.USER,
        actor_user_id=user.id,
        entity_type="product_suggestion",
        entity_id=suggestion.id,
        action="product_suggestion_rejected",
        detail={"sku": suggestion.sku, "reason": payload.reason},
    )
    await db.commit()
    return await _get_suggestion_or_404(db, tenant, suggestion.id)
