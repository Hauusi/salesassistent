"""RQ job bodies. RQ itself is synchronous, so each job wraps an async
implementation with asyncio.run.

The poll job is the system's only unattended entry point: nobody is
watching it, and everything it touches (Gmail, Claude, Voyage, Postgres)
can fail transiently or permanently. Its error handling is therefore part
of the contract, not an afterthought - see `_process_one_message`.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import async_session_factory, engine
from app.models.enums import ActionActor
from app.models.mailbox import Mailbox
from app.services import gmail_client
from app.services.action_log_service import log_action
from app.services.pipeline import process_incoming_email
from app.workers.queue import enqueue_poll

logger = logging.getLogger(__name__)


@dataclass
class PollResult:
    """Outcome of one poll cycle for one mailbox."""

    processed: int = 0
    already_known: int = 0
    failed: int = 0

    def __str__(self) -> str:  # shows up in the RQ job result
        return f"processed={self.processed} known={self.already_known} failed={self.failed}"


async def _process_one_message(
    db: AsyncSession, *, service, mailbox: Mailbox, message_id: str, result: PollResult
) -> None:
    """Fetches and processes a single message, absorbing any failure.

    A raised exception here used to abort the entire batch. Because
    `mailbox.last_synced_at` is only advanced after the loop, the next tick
    then re-fetched the same messages, failed on the same one again, and
    did so forever - every mail behind a single unprocessable one was never
    seen, with no signal beyond a failed RQ job.

    So: one bad message costs that message, and the failure is written to
    the audit trail where it is visible, rather than costing the mailbox.
    """
    try:
        fetched = await gmail_client.get_message(service, message_id)
        email = await process_incoming_email(db, mailbox=mailbox, fetched=fetched)
        if email is None:
            result.already_known += 1
        else:
            result.processed += 1
        await db.commit()
    except Exception as exc:  # noqa: BLE001 - deliberately broad, see docstring
        result.failed += 1
        logger.exception(
            "mail_processing_failed mailbox=%s gmail_message_id=%s", mailbox.id, message_id
        )
        await db.rollback()
        await _log_failure(db, mailbox=mailbox, message_id=message_id, exc=exc)


async def _log_failure(
    db: AsyncSession, *, mailbox: Mailbox, message_id: str, exc: Exception
) -> None:
    """Records a skipped message in the audit trail.

    Best-effort by design: if even this write fails, the log line above is
    still the record, and the poll cycle carries on.
    """
    try:
        await log_action(
            db,
            tenant_id=mailbox.tenant_id,
            actor=ActionActor.SYSTEM,
            entity_type="mailbox",
            entity_id=mailbox.id,
            action="mail_processing_failed",
            detail={
                "gmail_message_id": message_id,
                "error_type": type(exc).__name__,
                "error": str(exc)[:1000],
            },
        )
        await db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("failure_audit_write_failed mailbox=%s", mailbox.id)
        await db.rollback()


async def _poll_mailbox_async(mailbox_id: str) -> PollResult:
    result = PollResult()

    async with async_session_factory() as db:
        mailbox = await db.get(Mailbox, uuid.UUID(mailbox_id))
        if mailbox is None or not mailbox.is_active:
            return result

        service, creds = await gmail_client.get_gmail_service(mailbox)

        if gmail_client.apply_refreshed_credentials(mailbox, creds):
            await db.commit()

        after_query = None
        if mailbox.last_synced_at is not None:
            after_query = f"after:{int(mailbox.last_synced_at.timestamp())}"

        message_ids = await gmail_client.list_new_message_ids(
            service, after_query=after_query, max_results=get_settings().mail_poll_batch_size
        )

        for message_id in message_ids:
            # Commit happens per message, not once after the whole batch.
            # By that point real Claude tokens have been spent on
            # classification (and draft generation); discarding them on a
            # later message's failure would mean re-spending them on the
            # next cycle, because the idempotency check in
            # process_incoming_email keys on a row that was never persisted.
            await _process_one_message(
                db, service=service, mailbox=mailbox, message_id=message_id, result=result
            )

        # Always advance the watermark, including past messages that failed.
        # Those are recorded in the audit trail; leaving the watermark back
        # would retry them forever and block everything behind them.
        mailbox.last_synced_at = datetime.now(timezone.utc)
        await db.commit()

    logger.info("mailbox_poll_finished mailbox=%s %s", mailbox_id, result)
    return result


async def _run_and_dispose(coro):
    """Runs an async entry point and disposes the connection pool.

    asyncpg connections are bound to the event loop they were opened on.
    asyncio.run() tears its loop down on return, so pooled connections
    would be unusable ("attached to a different loop") the next time this
    process reuses the module-level `engine`. The scheduler is one
    long-lived process calling asyncio.run() every tick, which makes this
    an observed bug rather than a theoretical one.
    """
    try:
        return await coro
    finally:
        await engine.dispose()


def poll_mailbox_job(mailbox_id: str) -> str:
    """RQ entrypoint: fetches and processes new mail for one mailbox."""
    return str(asyncio.run(_run_and_dispose(_poll_mailbox_async(mailbox_id))))


async def _list_active_mailbox_ids() -> list[str]:
    async with async_session_factory() as db:
        result = await db.execute(select(Mailbox.id).where(Mailbox.is_active.is_(True)))
        return [str(mid) for mid in result.scalars().all()]


def enqueue_poll_for_all_active_mailboxes() -> int:
    """Called by the scheduler on each tick. Enqueues one poll job per
    connected, active mailbox, skipping any mailbox whose previous poll is
    still queued or running."""
    mailbox_ids = asyncio.run(_run_and_dispose(_list_active_mailbox_ids()))
    return sum(1 for mailbox_id in mailbox_ids if enqueue_poll(mailbox_id) is not None)
