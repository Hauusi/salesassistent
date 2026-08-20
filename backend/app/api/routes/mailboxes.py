from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_tenant
from app.db import get_db
from app.models.mailbox import Mailbox
from app.models.tenant import Tenant
from app.schemas.mailbox import MailboxOut, PollTriggerOut
from app.workers.queue import enqueue_poll

router = APIRouter(prefix="/api/mailboxes", tags=["mailboxes"])


@router.get("", response_model=list[MailboxOut])
async def list_mailboxes(
    db: AsyncSession = Depends(get_db), tenant: Tenant = Depends(get_current_tenant)
) -> list[Mailbox]:
    result = await db.execute(select(Mailbox).where(Mailbox.tenant_id == tenant.id))
    return list(result.scalars().all())


@router.post("/{mailbox_id}/poll-now", status_code=202, response_model=PollTriggerOut)
async def poll_now(
    mailbox_id: uuid.UUID, db: AsyncSession = Depends(get_db), tenant: Tenant = Depends(get_current_tenant)
) -> PollTriggerOut:
    """Manual trigger for local testing, so a developer doesn't have to
    wait for the next scheduler tick to see a test mail come through."""
    result = await db.execute(
        select(Mailbox).where(Mailbox.id == mailbox_id, Mailbox.tenant_id == tenant.id)
    )
    mailbox = result.scalar_one_or_none()
    if mailbox is None:
        raise HTTPException(status_code=404, detail="Postfach nicht gefunden.")

    job = enqueue_poll(str(mailbox.id))
    if job is None:
        # A poll for this mailbox is already queued or running - saying so
        # is more useful than silently stacking a second one.
        return PollTriggerOut(job_id=None, status="already_running")
    return PollTriggerOut(job_id=job.id, status="enqueued")
