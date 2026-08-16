"""Read access to classified mail for the dashboard's default inbox view.

spam_verdacht mail is excluded by default (hidden from the standard view,
per concept 5.3) but never deleted - pass include_spam=true to see it.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_tenant
from app.db import get_db
from app.models.email_message import EmailMessage
from app.models.enums import EmailStatus, TypKategorie, WichtigkeitsKategorie
from app.models.tenant import Tenant
from app.schemas.email import EmailOut

router = APIRouter(prefix="/api/emails", tags=["emails"])


@router.get("", response_model=list[EmailOut])
async def list_emails(
    wichtigkeit: WichtigkeitsKategorie | None = None,
    typ: TypKategorie | None = None,
    status: EmailStatus | None = None,
    include_spam: bool = False,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> list[EmailMessage]:
    stmt = (
        select(EmailMessage)
        .where(EmailMessage.tenant_id == tenant.id)
        .options(selectinload(EmailMessage.contact), selectinload(EmailMessage.case))
        .order_by(EmailMessage.received_at.desc())
        .limit(limit)
    )
    if not include_spam and status is None:
        stmt = stmt.where(EmailMessage.status != EmailStatus.AUSGEBLENDET)
    if wichtigkeit is not None:
        stmt = stmt.where(EmailMessage.wichtigkeits_kategorie == wichtigkeit)
    if typ is not None:
        stmt = stmt.where(EmailMessage.typ == typ)
    if status is not None:
        stmt = stmt.where(EmailMessage.status == status)

    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("/{email_id}", response_model=EmailOut)
async def get_email(
    email_id: uuid.UUID, db: AsyncSession = Depends(get_db), tenant: Tenant = Depends(get_current_tenant)
) -> EmailMessage:
    result = await db.execute(
        select(EmailMessage)
        .where(EmailMessage.id == email_id, EmailMessage.tenant_id == tenant.id)
        .options(selectinload(EmailMessage.contact), selectinload(EmailMessage.case))
    )
    email = result.scalar_one_or_none()
    if email is None:
        raise HTTPException(status_code=404, detail="Mail nicht gefunden.")
    return email
