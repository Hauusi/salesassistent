from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_tenant, get_current_user
from app.db import get_db
from app.models.case import Case, CaseContact
from app.models.email_message import EmailMessage
from app.models.enums import ActionActor, DealStage
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.case_detail import CaseDetailOut, CaseListItemOut, CaseStageUpdateIn
from app.services.action_log_service import log_action
from app.services.case_stage_service import set_deal_stage

router = APIRouter(prefix="/api/cases", tags=["cases"])


@router.get("", response_model=list[CaseListItemOut])
async def list_cases(
    deal_stage: DealStage | None = None,
    db: AsyncSession = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> list[CaseListItemOut]:
    stmt = (
        select(Case)
        .where(Case.tenant_id == tenant.id)
        .options(selectinload(Case.contacts).selectinload(CaseContact.contact))
        .order_by(Case.updated_at.desc())
    )
    if deal_stage is not None:
        stmt = stmt.where(Case.deal_stage == deal_stage)
    result = await db.execute(stmt)
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
            deal_stage=case.deal_stage,
            deal_stage_changed_at=case.deal_stage_changed_at,
            created_at=case.created_at,
            contacts=[cc.contact for cc in case.contacts],
            email_count=counts.get(case.id, 0),
        )
        for case in cases
    ]


async def _get_case_or_404(db: AsyncSession, tenant: Tenant, case_id: uuid.UUID) -> Case:
    result = await db.execute(
        select(Case)
        .where(Case.id == case_id, Case.tenant_id == tenant.id)
        .options(selectinload(Case.contacts).selectinload(CaseContact.contact))
    )
    case = result.scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="Case nicht gefunden.")
    return case


async def _case_detail_out(db: AsyncSession, case: Case) -> CaseDetailOut:
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
        deal_stage=case.deal_stage,
        deal_stage_changed_at=case.deal_stage_changed_at,
        created_at=case.created_at,
        contacts=[cc.contact for cc in case.contacts],
        email_count=len(emails),
        emails=emails,
    )


@router.get("/{case_id}", response_model=CaseDetailOut)
async def get_case(
    case_id: uuid.UUID, db: AsyncSession = Depends(get_db), tenant: Tenant = Depends(get_current_tenant)
) -> CaseDetailOut:
    case = await _get_case_or_404(db, tenant, case_id)
    return await _case_detail_out(db, case)


@router.patch("/{case_id}/stage", response_model=CaseDetailOut)
async def set_case_stage(
    case_id: uuid.UUID,
    payload: CaseStageUpdateIn,
    db: AsyncSession = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
    user: User = Depends(get_current_user),
) -> CaseDetailOut:
    """Manually marks a case GEWONNEN or VERLOREN - the only two stages a
    human sets directly (see app/services/case_stage_service.py); every
    other stage is pipeline-driven. `force=True` here deliberately: a
    human correcting a mistake (moving a case back out of GEWONNEN/
    VERLOREN, or between the two) must always be possible, unlike an
    automatic transition."""
    case = await _get_case_or_404(db, tenant, case_id)

    changed = set_deal_stage(case, payload.deal_stage, force=True)
    if changed:
        await log_action(
            db,
            tenant_id=tenant.id,
            actor=ActionActor.USER,
            actor_user_id=user.id,
            entity_type="case",
            entity_id=case.id,
            action="deal_stage_changed",
            detail={"deal_stage": payload.deal_stage.value, "reason": "manual"},
        )
        await db.commit()

    return await _case_detail_out(db, case)
