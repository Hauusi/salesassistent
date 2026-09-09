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
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import async_session_factory, engine
from app.models.enums import ActionActor, MailboxPollStatus
from app.models.mailbox import Mailbox
from app.services import gmail_client
from app.services.action_log_service import log_action
from app.services.case_stage_service import apply_stale_offer_transitions
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
    except Exception as exc:
        result.failed += 1
        logger.exception(
            "mail_processing_failed mailbox=%s gmail_message_id=%s", mailbox.id, message_id
        )
        await db.rollback()
        # rollback() expires every attribute on `mailbox` - a plain
        # attribute access afterwards (as opposed to this explicit,
        # async-safe refresh) triggers a synchronous lazy-load, which
        # raises MissingGreenlet on a real session. Without this, every
        # per-message failure crashed while writing its own audit-trail
        # entry below, and - since the caller's loop reuses this same
        # `mailbox` object for the next message_id - poisoned every
        # message behind it in the same batch too.
        await db.refresh(mailbox)
        await _log_failure(db, mailbox=mailbox, message_id=message_id, exc=exc)


async def _log_failure(
    db: AsyncSession, *, mailbox: Mailbox, message_id: str, exc: Exception
) -> None:
    """Records a skipped message in the audit trail.

    Best-effort by design: if even this write fails, the log line above is
    still the record, and the poll cycle carries on.
    """
    # Captured once, up front: if the write below fails, its except block
    # rolls back and expires every attribute on `mailbox` again, and
    # re-reading mailbox.id there (rather than this already-captured
    # local) would itself raise MissingGreenlet trying to lazy-load an
    # expired attribute outside of an await - the exact bug this whole
    # function otherwise falls into.
    mailbox_id = mailbox.id
    try:
        await log_action(
            db,
            tenant_id=mailbox.tenant_id,
            actor=ActionActor.SYSTEM,
            entity_type="mailbox",
            entity_id=mailbox_id,
            action="mail_processing_failed",
            detail={
                "gmail_message_id": message_id,
                "error_type": type(exc).__name__,
                "error": str(exc)[:1000],
            },
        )
        await db.commit()
    except Exception:
        logger.exception("failure_audit_write_failed mailbox=%s", mailbox_id)
        await db.rollback()
        try:
            # Same reasoning as the refresh in _process_one_message: this
            # rollback expires `mailbox` again, and the caller's loop
            # reuses this object for the next message_id. Swallowed on its
            # own failure - this is already the "even the audit log write
            # failed" fallback path, and the log line above is still the
            # record either way.
            await db.refresh(mailbox)
        except Exception:
            logger.exception("mailbox_refresh_after_failed_audit_write_failed mailbox=%s", mailbox_id)


async def _poll_mailbox_async(mailbox_id: str) -> PollResult:
    result = PollResult()

    async with async_session_factory() as db:
        mailbox = await db.get(Mailbox, uuid.UUID(mailbox_id))
        if mailbox is None or not mailbox.is_active:
            return result

        try:
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
            mailbox.last_synced_at = datetime.now(UTC)
            _mark_poll_outcome(mailbox, status=MailboxPollStatus.OK, error=None)
            await db.commit()
        except Exception as exc:
            # A crash here is job-level (Gmail auth, rate limits exhausted,
            # the watermark commit itself) - distinct from a per-message
            # failure, which _process_one_message already absorbed above
            # without raising. Previously this was visible only in
            # container logs; the mailbox's poll status is the only place a
            # human looking at the UI would ever see it.
            logger.exception("mailbox_poll_failed mailbox=%s", mailbox_id)
            await db.rollback()
            await _record_poll_failure(mailbox_id, exc)
            raise

    logger.info("mailbox_poll_finished mailbox=%s %s", mailbox_id, result)
    return result


def _mark_poll_outcome(
    mailbox: Mailbox, *, status: MailboxPollStatus, error: str | None
) -> None:
    mailbox.last_poll_status = status
    # Text column (unbounded), but an exception's str() can still run to
    # kilobytes (a provider's HTML error body, say) - capped for the same
    # reason _log_failure below caps its own audit-trail error field.
    mailbox.last_poll_error_message = error[:2000] if error else None
    mailbox.last_poll_at = datetime.now(UTC)


async def _record_poll_failure(mailbox_id: str, exc: Exception) -> None:
    """Writes the poll failure on a fresh session, independent of whatever
    state the failed attempt's own session/transaction ended up in - e.g. a
    DB-connection-level failure would otherwise also break this write.

    Best-effort: if even this fails, the exception this function was
    called for still propagates and lands in the logs either way.
    """
    try:
        async with async_session_factory() as db:
            mailbox = await db.get(Mailbox, uuid.UUID(mailbox_id))
            if mailbox is None:
                return
            _mark_poll_outcome(mailbox, status=MailboxPollStatus.ERROR, error=str(exc))
            await db.commit()
    except Exception:
        logger.exception("mailbox_poll_failure_record_failed mailbox=%s", mailbox_id)


async def _run_and_dispose(coro):
    """Runs an async entry point on a clean connection pool and disposes it
    again afterwards.

    asyncpg connections are bound to the event loop they were opened on.
    asyncio.run() tears its loop down on return, so any connection left in
    the module-level `engine`'s pool is unusable the next time this process
    reuses `engine` - the checkout raises MissingGreenlet ("attached to a
    different loop") deep inside pool_pre_ping, on the very first query.
    The scheduler is one long-lived process calling asyncio.run() every
    tick, which makes this an observed bug rather than a theoretical one -
    and the same is true of an RQ worker that does not fork a fresh process
    per job (SimpleWorker, or forking disabled), or of a job that runs right
    after a crash skipped the previous run's cleanup.

    Disposing only *after* the run (the previous fix here) protects the
    *next* call but not this one: if this process's engine already carries
    connections from an earlier loop - the scheduler's previous tick, a
    prior job, a crash that never reached this `finally` - the very first
    checkout in `coro` inherits them. Disposing before as well guarantees
    every run starts with an empty pool, regardless of what happened
    earlier in this process.
    """
    await engine.dispose()
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


async def _apply_stale_offer_transitions_and_commit() -> int:
    async with async_session_factory() as db:
        count = await apply_stale_offer_transitions(db)
        await db.commit()
        return count


def run_case_stage_sweep() -> int:
    """Called by the scheduler on each tick, alongside the mail poll
    enqueue above. NACHFASSEN is the one deal_stage transition that fires
    on the *absence* of an event (no reply within N days) rather than on
    one - see app/services/case_stage_service.py - so it needs a periodic
    sweep instead of a call site to hook into."""
    return asyncio.run(_run_and_dispose(_apply_stale_offer_transitions_and_commit()))
