from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_tenant
from app.db import get_db
from app.models.case import Case, CaseContact
from app.models.email_message import EmailMessage
from app.models.tenant import Tenant
from app.schemas.case_detail import CaseDetailOut, CaseListItemOut

router = APIRouter(prefix="/api/cases", tags=["cases"])


@router.get("", response_model=list[CaseListItemOut])
async def list_cases(
    db: AsyncSession = Depends(get_db), tenant: Tenant = Depends(get_current_tenant)
) -> list[CaseListItemOut]:
    result = await db.execute(
        select(Case)
        .where(Case.tenant_id == tenant.id)
        .options(selectinload(Case.contacts).selectinload(CaseContact.contact))
        .order_by(Case.updated_at.desc())
    )
    cases = list(result.scalars().unique().all())

    count_result = await db.execute(
        select(EmailMessage.case_id, func.count(EmailMessage.id))
        .where(EmailMessage.tenant_id == tenant.id, EmailMessage.case_id.isnot(None))
        .group_by(EmailMessage.case_id)
    )
    counts = dict(count_result.all())

    return [
        CaseListItemOut(
            id=case.id,
            title=case.title,
            summary=case.summary,
            status=case.status,
            created_at=case.created_at,
            contacts=[cc.contact for cc in case.contacts],
            email_count=counts.get(case.id, 0),
        )
        for case in cases
    ]


@router.get("/{case_id}", response_model=CaseDetailOut)
async def get_case(
    case_id: uuid.UUID, db: AsyncSession = Depends(get_db), tenant: Tenant = Depends(get_current_tenant)
) -> CaseDetailOut:
    result = await db.execute(
        select(Case)
        .where(Case.id == case_id, Case.tenant_id == tenant.id)
        .options(selectinload(Case.contacts).selectinload(CaseContact.contact))
    )
    case = result.scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="Case nicht gefunden.")

    emails_result = await db.execute(
        select(EmailMessage)
        .where(EmailMessage.case_id == case.id)
        .options(selectinload(EmailMessage.contact), selectinload(EmailMessage.case))
        .order_by(EmailMessage.received_at.desc())
    )
    emails = list(emails_result.scalars().all())

    return CaseDetailOut(
        id=case.id,
        title=case.title,
        summary=case.summary,
        status=case.status,
        created_at=case.created_at,
        contacts=[cc.contact for cc in case.contacts],
        email_count=len(emails),
        emails=emails,
    )
