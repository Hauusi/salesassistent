"""Turns a detected article number + text from an incoming mail into a
ProductSuggestion pending human review - see app/models/product_suggestion.py
and app/api/routes/product_suggestions.py for the approve/reject workflow.

Detection itself piggybacks on the existing classify_email tool call (see
app/services/classification.py) rather than a separate Claude call, so this
module never talks to the model - it only ever turns an already-paid-for
result into a DB row.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email_message import EmailMessage
from app.models.enums import ProductSuggestionStatus
from app.models.limits import fit
from app.models.product import Product
from app.models.product_suggestion import ProductSuggestion
from app.services.classification import ClassificationResult


async def maybe_create_suggestion(
    db: AsyncSession, *, tenant_id: uuid.UUID, email: EmailMessage, result: ClassificationResult
) -> ProductSuggestion | None:
    """Creates a ProductSuggestion if this mail's classification detected a
    new article. Returns None (and writes nothing) when:

    - no article number was detected, or it came without a description
      (an order/inquiry referencing an *existing* SKU is not a detection -
      see the tool schema in app/services/classification.py);
    - that SKU already exists in the tenant's catalog - nothing to suggest;
    - a suggestion for that SKU is already pending review, so the same new
      article mentioned across several mails does not pile up duplicate
      suggestions for a human to review one at a time.

    Does not commit - the caller (app/services/pipeline.py) owns the
    transaction boundary, same as every other write in
    process_incoming_email.
    """
    sku = result.detected_product_sku
    description = result.detected_product_description
    if not sku or not description:
        return None

    already_in_catalog = (
        await db.execute(
            select(Product.id).where(Product.tenant_id == tenant_id, Product.sku == sku)
        )
    ).scalar_one_or_none()
    if already_in_catalog is not None:
        return None

    already_pending = (
        await db.execute(
            select(ProductSuggestion.id).where(
                ProductSuggestion.tenant_id == tenant_id,
                ProductSuggestion.sku == sku,
                ProductSuggestion.status == ProductSuggestionStatus.VORGESCHLAGEN,
            )
        )
    ).scalar_one_or_none()
    if already_pending is not None:
        return None

    suggestion = ProductSuggestion(
        tenant_id=tenant_id,
        email_message_id=email.id,
        sku=fit(sku, ProductSuggestion, "sku"),
        name=fit(result.detected_product_name or sku, ProductSuggestion, "name"),
        description=description,
    )
    db.add(suggestion)
    await db.flush()
    return suggestion
