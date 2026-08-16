from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_tenant
from app.db import get_db
from app.models.mailbox import Mailbox
from app.models.tenant import Tenant
from app.schemas.mailbox import MailboxOut

router = APIRouter(prefix="/api/mailboxes", tags=["mailboxes"])


@router.get("", response_model=list[MailboxOut])
async def list_mailboxes(
    db: AsyncSession = Depends(get_db), tenant: Tenant = Depends(get_current_tenant)
) -> list[Mailbox]:
    result = await db.execute(select(Mailbox).where(Mailbox.tenant_id == tenant.id))
    return list(result.scalars().all())


@router.post("/{mailbox_id}/poll-now", status_code=202)
async def poll_now(
    mailbox_id: uuid.UUID, db: AsyncSession = Depends(get_db), tenant: Tenant = Depends(get_current_tenant)
) -> dict:
    """Manual trigger for local testing, so a developer doesn't have to
    wait for the next scheduler tick to see a test mail come through."""
    result = await db.execute(
        select(Mailbox).where(Mailbox.id == mailbox_id, Mailbox.tenant_id == tenant.id)
    )
    mailbox = result.scalar_one_or_none()
    if mailbox is None:
        raise HTTPException(status_code=404, detail="Postfach nicht gefunden.")

    from app.workers.queue import get_queue
    from app.workers.tasks import poll_mailbox_job

    job = get_queue().enqueue(poll_mailbox_job, str(mailbox.id), job_timeout=300)
    return {"job_id": job.id}
