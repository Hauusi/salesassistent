"""RQ job bodies. RQ itself is synchronous, so each job wraps an async
implementation with asyncio.run.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.db import async_session_factory, engine
from app.models.mailbox import Mailbox
from app.services import gmail_client
from app.services.pipeline import process_incoming_email

logger = logging.getLogger(__name__)


async def _poll_mailbox_async(mailbox_id: str) -> int:
    processed_count = 0
    async with async_session_factory() as db:
        mailbox = await db.get(Mailbox, uuid.UUID(mailbox_id))
        if mailbox is None or not mailbox.is_active:
            return 0

        service, creds = await gmail_client.get_gmail_service(mailbox)

        # Persist a rotated access token if the client refreshed it.
        refreshed_fields = gmail_client.encrypted_fields_from_credentials(creds)
        if refreshed_fields["access_token_encrypted"] != mailbox.access_token_encrypted:
            for key, value in refreshed_fields.items():
                setattr(mailbox, key, value)
            await db.commit()

        after_query = None
        if mailbox.last_synced_at is not None:
            after_query = f"after:{int(mailbox.last_synced_at.timestamp())}"

        message_ids = await gmail_client.list_new_message_ids(service, after_query=after_query)

        for message_id in message_ids:
            fetched = await gmail_client.get_message(service, message_id)
            result = await process_incoming_email(db, mailbox=mailbox, fetched=fetched)
            if result is not None:
                processed_count += 1

        mailbox.last_synced_at = datetime.now(timezone.utc)
        await db.commit()

    return processed_count


async def _poll_mailbox_and_dispose(mailbox_id: str) -> int:
    try:
        return await _poll_mailbox_async(mailbox_id)
    finally:
        # asyncpg connections are bound to the event loop they were
        # opened on. asyncio.run() below tears down its loop when this
        # call returns, so the pooled connections it opened would be
        # unusable (and raise "attached to a different loop") the next
        # time this process reuses the module-level `engine`. Dispose the
        # pool before the loop closes so the next call starts clean.
        # Cheap even though RQ normally forks a fresh process per job -
        # protects a SimpleWorker/--burst deployment too.
        await engine.dispose()


def poll_mailbox_job(mailbox_id: str) -> int:
    """RQ entrypoint: fetches and processes new mail for one mailbox."""
    return asyncio.run(_poll_mailbox_and_dispose(mailbox_id))


async def _poll_all_active_mailboxes_async() -> list[str]:
    async with async_session_factory() as db:
        result = await db.execute(select(Mailbox.id).where(Mailbox.is_active.is_(True)))
        return [str(mid) for mid in result.scalars().all()]


async def _list_active_mailboxes_and_dispose() -> list[str]:
    try:
        return await _poll_all_active_mailboxes_async()
    finally:
        # Same "different loop" hazard as above, but here it's the
        # observed bug: the scheduler is one long-lived process that
        # calls asyncio.run() every tick without ever exiting, so this
        # engine.dispose() runs on essentially every poll cycle.
        await engine.dispose()


def enqueue_poll_for_all_active_mailboxes() -> int:
    """Called by the scheduler on each tick. Enqueues one poll job per
    connected, active mailbox."""
    from app.workers.queue import get_queue

    mailbox_ids = asyncio.run(_list_active_mailboxes_and_dispose())
    queue = get_queue()
    for mailbox_id in mailbox_ids:
        queue.enqueue(poll_mailbox_job, mailbox_id, job_timeout=300)
    return len(mailbox_ids)
