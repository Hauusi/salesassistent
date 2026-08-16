"""Wissensbasis: searchable view of filed "information" mails, grouped by
contact and case (concept scope item 5).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_tenant
from app.db import get_db
from app.models.email_message import EmailMessage
from app.models.enums import WichtigkeitsKategorie
from app.models.tenant import Tenant
from app.schemas.email import EmailOut

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
