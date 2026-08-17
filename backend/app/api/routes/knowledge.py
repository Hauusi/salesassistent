"""Wissensbasis: searchable view of filed "information" mails, grouped by
contact and case (concept scope item 5) - plus the product catalog that
grounds draft generation on Angebotsanfragen (see
app/services/product_search.py).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_tenant
from app.db import get_db
from app.models.email_message import EmailMessage
from app.models.enums import WichtigkeitsKategorie
from app.models.product import Product
from app.models.tenant import Tenant
from app.schemas.email import EmailOut
from app.schemas.product import ProductCreateIn, ProductImportResult, ProductOut, ProductUpdateIn
from app.services.product_import import import_products_csv

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


@router.get("/search", response_model=list[EmailOut])
async def search_knowledge(
    q: str | None = None,
    contact_id: uuid.UUID | None = None,
    case_id: uuid.UUID | None = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> list[EmailMessage]:
    stmt = (
        select(EmailMessage)
        .where(
            EmailMessage.tenant_id == tenant.id,
            EmailMessage.wichtigkeits_kategorie == WichtigkeitsKategorie.INFORMATION,
        )
        .options(selectinload(EmailMessage.contact), selectinload(EmailMessage.case))
        .order_by(EmailMessage.received_at.desc())
        .limit(limit)
    )
    if contact_id is not None:
        stmt = stmt.where(EmailMessage.contact_id == contact_id)
    if case_id is not None:
        stmt = stmt.where(EmailMessage.case_id == case_id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                EmailMessage.subject.ilike(like),
                EmailMessage.raw_content.ilike(like),
                EmailMessage.sender_address.ilike(like),
            )
        )

    result = await db.execute(stmt)
    return list(result.scalars().all())


# --- Produkte (Wissensbasis) -------------------------------------------

async def _get_product_or_404(db: AsyncSession, tenant: Tenant, product_id: uuid.UUID) -> Product:
    result = await db.execute(
        select(Product).where(Product.id == product_id, Product.tenant_id == tenant.id)
    )
    product = result.scalar_one_or_none()
    if product is None:
        raise HTTPException(status_code=404, detail="Produkt nicht gefunden.")
    return product


@router.get("/products", response_model=list[ProductOut])
async def list_products(
    q: str | None = None,
    category: str | None = None,
    limit: int = 200,
    db: AsyncSession = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> list[Product]:
    stmt = (
        select(Product)
        .where(Product.tenant_id == tenant.id)
        .order_by(Product.name)
        .limit(limit)
    )
    if category:
        stmt = stmt.where(Product.category == category)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(Product.name.ilike(like), Product.description.ilike(like), Product.category.ilike(like))
        )
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.post("/products", response_model=ProductOut, status_code=201)
async def create_product(
    payload: ProductCreateIn,
    db: AsyncSession = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> Product:
    product = Product(tenant_id=tenant.id, **payload.model_dump())
    db.add(product)
    await db.commit()
    await db.refresh(product)
    return product


@router.post("/products/import", response_model=ProductImportResult)
async def import_products(
    file: UploadFile,
    db: AsyncSession = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> ProductImportResult:
    if file.content_type not in (None, "text/csv", "application/vnd.ms-excel", "application/octet-stream"):
        raise HTTPException(status_code=400, detail=f"Unerwarteter Dateityp: {file.content_type}")

    raw = await file.read()
    try:
        csv_text = raw.decode("utf-8-sig")  # tolerate Excel's BOM-prefixed UTF-8 exports
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="CSV-Datei konnte nicht als UTF-8 gelesen werden.") from exc

    result = await import_products_csv(db, tenant_id=tenant.id, csv_text=csv_text)
    await db.commit()
    return result


@router.put("/products/{product_id}", response_model=ProductOut)
async def update_product(
    product_id: uuid.UUID,
    payload: ProductUpdateIn,
    db: AsyncSession = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> Product:
    product = await _get_product_or_404(db, tenant, product_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(product, key, value)
    await db.commit()
    await db.refresh(product)
    return product


@router.delete("/products/{product_id}", status_code=204, response_model=None)
async def delete_product(
    product_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> None:
    product = await _get_product_or_404(db, tenant, product_id)
    await db.delete(product)
    await db.commit()
