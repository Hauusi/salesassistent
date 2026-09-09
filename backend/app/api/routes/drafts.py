"""Approval workflow: list open drafts, edit, approve (-> sends via Gmail),
or reject. Sending only ever happens here, after explicit human approval -
never automatically.

Approval is the one irreversible action in the system: once Gmail accepts
a message, no amount of local state can un-send it. `approve_draft` is
therefore written so that every failure mode ends in "not sent" or "sent
exactly once", never "sent twice" - see the two-phase claim there.
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
from app.models.draft import Draft
from app.models.email_message import EmailMessage
from app.models.enums import ActionActor, DealStage, DraftStatus, EmailStatus
from app.models.mailbox import Mailbox
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.draft import DraftOut, DraftRejectIn, DraftUpdateIn
from app.services import gmail_client
from app.services.action_log_service import log_action
from app.services.case_stage_service import set_deal_stage

router = APIRouter(prefix="/api/drafts", tags=["drafts"])


_EMAIL_RELATIONS = (
    selectinload(Draft.email_message).selectinload(EmailMessage.contact),
    selectinload(Draft.email_message).selectinload(EmailMessage.case),
)


async def _get_draft_or_404(
    db: AsyncSession, tenant: Tenant, draft_id: uuid.UUID, *, for_update: bool = False
) -> Draft:
    stmt = select(Draft).where(Draft.id == draft_id, Draft.tenant_id == tenant.id)
    if for_update:
        # Serialises concurrent approvals of the same draft. The lock is
        # taken on the Draft row only and held just long enough to claim it
        # (see approve_draft) - never across the Gmail call.
        stmt = stmt.with_for_update()
    stmt = stmt.options(*_EMAIL_RELATIONS)

    draft = (await db.execute(stmt)).scalar_one_or_none()
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
        .options(*_EMAIL_RELATIONS)
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
        raise HTTPException(
            status_code=409,
            detail="Nur Entwürfe im Status 'entwurf' können bearbeitet werden.",
        )

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
    # Reload through the same eager options rather than db.refresh():
    # refresh() expires the email_message relationship, and DraftOut
    # serialises it, which then lazy-loads inside the async request and
    # raises MissingGreenlet - a 500 on every successful mutation.
    return await _get_draft_or_404(db, tenant, draft.id)


@router.post("/{draft_id}/approve", response_model=DraftOut)
async def approve_draft(
    draft_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
    user: User = Depends(get_current_user),
) -> Draft:
    """Sends an approved draft, exactly once.

    Two phases, because sending is irreversible:

    1. Claim. The draft row is locked, checked, moved to FREIGEGEBEN and
       committed. The lock is released immediately; the claim is durable.
       A second, concurrent approval now blocks on the lock, then sees a
       status that is no longer ENTWURF and is rejected - where previously
       both requests passed the check and the customer got the mail twice.
    2. Send, then record. Only after Gmail accepts does the draft move to
       VERSENDET and the mail to ERLEDIGT.

    If the process dies between the two phases the draft stays in
    FREIGEGEBEN, visible and recoverable by a human. That is the
    deliberate direction of the trade: a stuck draft can be fixed, a
    duplicate mail to a customer cannot be un-sent.
    """
    draft = await _get_draft_or_404(db, tenant, draft_id, for_update=True)
    if draft.status != DraftStatus.ENTWURF:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Entwurf ist im Status '{draft.status.value}' - "
                "nur 'entwurf' kann freigegeben werden."
            ),
        )

    email = draft.email_message
    mailbox = (
        await db.execute(
            select(Mailbox).where(
                Mailbox.id == email.mailbox_id, Mailbox.tenant_id == tenant.id
            )
        )
    ).scalar_one_or_none()
    if mailbox is None:
        raise HTTPException(status_code=404, detail="Postfach für diese Mail nicht gefunden.")

    # Phase 1: claim.
    draft.status = DraftStatus.FREIGEGEBEN
    draft.approved_by_user_id = user.id
    draft.approved_at = datetime.now(UTC)
    await log_action(
        db,
        tenant_id=tenant.id,
        actor=ActionActor.USER,
        actor_user_id=user.id,
        entity_type="draft",
        entity_id=draft.id,
        action="draft_approved",
        detail={},
    )
    await db.commit()

    # Phase 2: send. Every path from here must leave the draft in a state a
    # human can act on.
    try:
        service, creds = await gmail_client.get_gmail_service(mailbox)
        gmail_client.apply_refreshed_credentials(mailbox, creds)

        sent_message_id = await gmail_client.send_reply(
            service,
            to_address=email.sender_address,
            subject=draft.subject or f"Re: {email.subject or ''}",
            body=draft.body,
            thread_id=email.gmail_thread_id,
            in_reply_to_rfc822_id=email.rfc822_message_id,
            from_address=mailbox.email_address,
        )
    except Exception as exc:
        # Nothing was sent, so releasing the claim is safe and lets the
        # user retry. This is also why send_reply must never raise after
        # Gmail has accepted the message.
        await db.rollback()
        draft.status = DraftStatus.ENTWURF
        draft.approved_by_user_id = None
        draft.approved_at = None
        await log_action(
            db,
            tenant_id=tenant.id,
            actor=ActionActor.SYSTEM,
            entity_type="draft",
            entity_id=draft.id,
            action="draft_send_failed",
            detail={"error_type": type(exc).__name__, "error": str(exc)[:1000]},
        )
        await db.commit()
        # The full provider error goes to the audit trail and the log, not
        # to the client: a Gmail HttpError body can carry internal URLs,
        # project identifiers and quota details, and this API has no
        # authentication in front of it.
        raise HTTPException(
            status_code=502,
            detail="Versand über Gmail fehlgeschlagen. Details siehe Protokoll.",
        ) from exc

    draft.sent_at = datetime.now(UTC)
    draft.gmail_sent_message_id = sent_message_id
    draft.status = DraftStatus.VERSENDET
    email.status = EmailStatus.ERLEDIGT

    # Pipeline stage: a reply just went out, so the case moves to
    # ANGEBOT_ERSTELLT (see app/services/case_stage_service.py). Guarded by
    # set_deal_stage's own terminal-stage check, so approving a draft on an
    # already GEWONNEN/VERLOREN case can't silently reopen it.
    if email.case is not None and set_deal_stage(email.case, DealStage.ANGEBOT_ERSTELLT):
        await log_action(
            db,
            tenant_id=tenant.id,
            actor=ActionActor.SYSTEM,
            entity_type="case",
            entity_id=email.case.id,
            action="deal_stage_changed",
            detail={"deal_stage": DealStage.ANGEBOT_ERSTELLT.value, "reason": "draft_sent"},
        )

    await log_action(
        db,
        tenant_id=tenant.id,
        actor=ActionActor.USER,
        actor_user_id=user.id,
        entity_type="draft",
        entity_id=draft.id,
        action="draft_sent",
        detail={"gmail_sent_message_id": sent_message_id},
    )
    await db.commit()
    # Reload through the same eager options rather than db.refresh():
    # refresh() expires the email_message relationship, and DraftOut
    # serialises it, which then lazy-loads inside the async request and
    # raises MissingGreenlet - a 500 on every successful mutation.
    return await _get_draft_or_404(db, tenant, draft.id)


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
        raise HTTPException(
            status_code=409,
            detail="Nur Entwürfe im Status 'entwurf' können abgelehnt werden.",
        )

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
    # Reload through the same eager options rather than db.refresh():
    # refresh() expires the email_message relationship, and DraftOut
    # serialises it, which then lazy-loads inside the async request and
    # raises MissingGreenlet - a 500 on every successful mutation.
    return await _get_draft_or_404(db, tenant, draft.id)
