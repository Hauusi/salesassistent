"""Approval workflow: list open drafts, edit, approve (-> sends via Gmail),
or reject. Sending only ever happens here, after explicit human approval -
never automatically.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_tenant, get_current_user
from app.db import get_db
from app.models.draft import Draft
from app.models.email_message import EmailMessage
from app.models.enums import ActionActor, DraftStatus, EmailStatus
from app.models.mailbox import Mailbox
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.draft import DraftOut, DraftRejectIn, DraftUpdateIn
from app.services import gmail_client
from app.services.action_log_service import log_action

router = APIRouter(prefix="/api/drafts", tags=["drafts"])


async def _get_draft_or_404(db: AsyncSession, tenant: Tenant, draft_id: uuid.UUID) -> Draft:
    result = await db.execute(
        select(Draft)
        .where(Draft.id == draft_id, Draft.tenant_id == tenant.id)
        .options(
            selectinload(Draft.email_message).selectinload(EmailMessage.contact),
            selectinload(Draft.email_message).selectinload(EmailMessage.case),
        )
    )
    draft = result.scalar_one_or_none()
    if draft is None:
        raise HTTPException(status_code=404, detail="Entwurf nicht gefunden.")
    return draft


@router.get("", response_model=list[DraftOut])
async def list_drafts(
    status: DraftStatus | None = None,
    db: AsyncSession = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> list[Draft]:
    stmt = (
        select(Draft)
        .where(Draft.tenant_id == tenant.id)
        .options(
            selectinload(Draft.email_message).selectinload(EmailMessage.contact),
            selectinload(Draft.email_message).selectinload(EmailMessage.case),
        )
        .order_by(Draft.created_at.desc())
    )
    stmt = stmt.where(Draft.status == status) if status else stmt.where(Draft.status == DraftStatus.ENTWURF)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("/{draft_id}", response_model=DraftOut)
async def get_draft(
    draft_id: uuid.UUID, db: AsyncSession = Depends(get_db), tenant: Tenant = Depends(get_current_tenant)
) -> Draft:
    return await _get_draft_or_404(db, tenant, draft_id)


@router.put("/{draft_id}", response_model=DraftOut)
async def update_draft(
    draft_id: uuid.UUID,
    payload: DraftUpdateIn,
    db: AsyncSession = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
    user: User = Depends(get_current_user),
) -> Draft:
    draft = await _get_draft_or_404(db, tenant, draft_id)
    if draft.status != DraftStatus.ENTWURF:
        raise HTTPException(status_code=409, detail="Nur Entwürfe im Status 'entwurf' können bearbeitet werden.")

    draft.subject = payload.subject
    draft.body = payload.body
    await log_action(
        db,
        tenant_id=tenant.id,
        actor=ActionActor.USER,
        actor_user_id=user.id,
        entity_type="draft",
        entity_id=draft.id,
        action="draft_edited",
        detail={},
    )
    await db.commit()
    await db.refresh(draft)
    return draft


@router.post("/{draft_id}/approve", response_model=DraftOut)
async def approve_draft(
    draft_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
    user: User = Depends(get_current_user),
) -> Draft:
    draft = await _get_draft_or_404(db, tenant, draft_id)
    if draft.status != DraftStatus.ENTWURF:
        raise HTTPException(status_code=409, detail="Nur Entwürfe im Status 'entwurf' können freigegeben werden.")

    email = draft.email_message
    mailbox = await db.get(Mailbox, email.mailbox_id)
    if mailbox is None:
        raise HTTPException(status_code=500, detail="Postfach für diese Mail nicht gefunden.")

    service, creds = await gmail_client.get_gmail_service(mailbox)
    refreshed_fields = gmail_client.encrypted_fields_from_credentials(creds)
    if refreshed_fields["access_token_encrypted"] != mailbox.access_token_encrypted:
        for key, value in refreshed_fields.items():
            setattr(mailbox, key, value)

    try:
        sent_message_id = await gmail_client.send_reply(
            service,
            to_address=email.sender_address,
            subject=draft.subject or f"Re: {email.subject or ''}",
            body=draft.body,
            thread_id=email.gmail_thread_id,
            in_reply_to_rfc822_id=email.rfc822_message_id,
            from_address=mailbox.email_address,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    now = datetime.now(timezone.utc)
    draft.approved_by_user_id = user.id
    draft.approved_at = now
    draft.sent_at = now
    draft.gmail_sent_message_id = sent_message_id
    # Sending happens synchronously with approval in this MVP, so the
    # draft moves straight to the terminal "versendet" state.
    draft.status = DraftStatus.VERSENDET
    email.status = EmailStatus.ERLEDIGT

    await log_action(
        db,
        tenant_id=tenant.id,
        actor=ActionActor.USER,
        actor_user_id=user.id,
        entity_type="draft",
        entity_id=draft.id,
        action="draft_approved_and_sent",
        detail={"gmail_sent_message_id": sent_message_id},
    )
    await db.commit()
    await db.refresh(draft)
    return draft


@router.post("/{draft_id}/reject", response_model=DraftOut)
async def reject_draft(
    draft_id: uuid.UUID,
    payload: DraftRejectIn,
    db: AsyncSession = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
    user: User = Depends(get_current_user),
) -> Draft:
    draft = await _get_draft_or_404(db, tenant, draft_id)
    if draft.status != DraftStatus.ENTWURF:
        raise HTTPException(status_code=409, detail="Nur Entwürfe im Status 'entwurf' können abgelehnt werden.")

    draft.status = DraftStatus.ABGELEHNT
    draft.rejected_reason = payload.reason

    await log_action(
        db,
        tenant_id=tenant.id,
        actor=ActionActor.USER,
        actor_user_id=user.id,
        entity_type="draft",
        entity_id=draft.id,
        action="draft_rejected",
        detail={"reason": payload.reason},
    )
    await db.commit()
    await db.refresh(draft)
    return draft
