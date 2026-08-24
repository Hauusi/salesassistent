"""True end-to-end test of `poll_mailbox_job`, the RQ entrypoint itself -
asyncio.run() and the module-level connection pool included - not just the
inner async coroutine a unit test can await directly.

Regression: the module-level `engine` (app/db.py) is a process-global.
asyncpg connections are bound to the event loop they were opened on, and
asyncio.run() tears its loop down on return. Any connection left in the
pool from an earlier asyncio.run()-driven call in this process - the
scheduler's previous tick, an earlier job in a worker that does not fork
per job, or simply this test's own fixture setup - is unusable the moment
a new asyncio.run() call tries to check it out again. The checkout crashed
with

    sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been called;
    can't call await_only() here. Was IO attempted in an unexpected place?

(or the closely related "attached to a different loop" RuntimeError,
depending on exactly where the stale connection gets touched first) on the
very first DB read of the job - see app/workers/tasks.py::_run_and_dispose.

The tests below call `tasks.poll_mailbox_job` from a separate thread (via
asyncio.to_thread), which gives it a genuinely separate event loop from
this test's own - the same relationship an RQ worker process has to
whatever last used `app.db.engine`, and the exact condition needed to
reproduce the crash.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db import async_session_factory
from app.models.email_message import EmailMessage
from app.models.enums import TypKategorie, WichtigkeitsKategorie
from app.models.mailbox import Mailbox
from app.models.tenant import Tenant
from app.models.user import User
from app.services import gmail_client, pipeline
from app.services.classification import ClassificationResult
from app.workers import tasks


class _FakeCredentials:
    """Enough of google.oauth2.credentials.Credentials for
    gmail_client.apply_refreshed_credentials to read without raising."""

    token = None
    refresh_token = None
    expiry = None
    scopes = None


@pytest.fixture(autouse=True)
def _stub_boundaries(monkeypatch):
    """Stubs every outward-facing boundary the poll job crosses (Gmail,
    Claude, Voyage). This test is about the job's own DB/async plumbing,
    not about those integrations - see test_worker_tasks.py for the
    per-message failure-handling contract."""

    async def _get_gmail_service(_mailbox):
        return object(), _FakeCredentials()

    async def _list_new_message_ids(_service, after_query=None, max_results=25):
        return ["gm-e2e-1"]

    async def _get_message(_service, message_id):
        return gmail_client.FetchedEmail(
            gmail_message_id=message_id,
            gmail_thread_id="thread-1",
            rfc822_message_id=f"<{message_id}@example.de>",
            subject="Betreff",
            sender_address="kunde@example.com",
            sender_name="Kunde",
            raw_content="Guten Tag, eine Frage.",
            snippet="Guten Tag",
            received_at=datetime.now(UTC),
        )

    async def _classify(**_kwargs) -> ClassificationResult:
        return ClassificationResult(
            wichtigkeits_kategorie=WichtigkeitsKategorie.INFORMATION,
            typ=TypKategorie.KEINER,
            confidence=0.9,
            reasoning="Test",
        )

    async def _embed(*_args, **_kwargs) -> list[float]:
        return [0.1] * 1024

    # Patched on the shared module objects, not on `tasks.gmail_client` /
    # `pipeline.classification` separately - both are the same module
    # instances tasks.py and pipeline.py imported, so one patch covers both.
    monkeypatch.setattr(gmail_client, "get_gmail_service", _get_gmail_service)
    monkeypatch.setattr(gmail_client, "list_new_message_ids", _list_new_message_ids)
    monkeypatch.setattr(gmail_client, "get_message", _get_message)
    monkeypatch.setattr(pipeline.classification, "classify_email", _classify)
    monkeypatch.setattr(pipeline.embeddings, "embed_text", _embed)


@pytest.fixture
async def committed_mailbox(_schema) -> str:
    """A mailbox actually committed to Postgres, not just held in a
    SAVEPOINT.

    `poll_mailbox_job` opens its own session from the module-level pool, on
    its own event loop - it must see this data exactly the way the real RQ
    worker process would, which the `db_session` fixture's rolled-back
    outer transaction cannot provide. `_schema`'s drop_all teardown removes
    this real commit along with everything else, so no manual cleanup is
    needed here.
    """
    async with async_session_factory() as db:
        tenant = Tenant(name="Test", slug=f"test-{uuid.uuid4().hex[:8]}")
        db.add(tenant)
        await db.flush()
        user = User(tenant_id=tenant.id, email="user@example.com")
        db.add(user)
        await db.flush()
        mailbox = Mailbox(
            tenant_id=tenant.id,
            user_id=user.id,
            email_address="me@example.com",
            last_synced_at=datetime.now(UTC) - timedelta(hours=1),
        )
        db.add(mailbox)
        await db.commit()
        return str(mailbox.id)


async def test_poll_mailbox_job_runs_end_to_end_without_crashing(
    committed_mailbox: str,
) -> None:
    """The real RQ entrypoint - asyncio.run(), the module-level engine, all
    of it - must complete and actually persist the fetched mail, not crash
    on DB-connection checkout."""
    result = await asyncio.to_thread(tasks.poll_mailbox_job, committed_mailbox)

    assert result == "processed=1 known=0 failed=0"

    async with async_session_factory() as db:
        stored = (
            await db.execute(
                select(EmailMessage.gmail_message_id).where(
                    EmailMessage.mailbox_id == uuid.UUID(committed_mailbox)
                )
            )
        ).scalars().all()
        mailbox = await db.get(Mailbox, uuid.UUID(committed_mailbox))

    assert stored == ["gm-e2e-1"]
    assert mailbox is not None and mailbox.last_synced_at is not None


async def test_poll_mailbox_job_survives_a_previous_runs_lost_cleanup(
    committed_mailbox: str,
) -> None:
    """Regression for the exact crash this fix addresses.

    `_run_and_dispose` already disposed the pool *after* every run before
    this fix - which is enough as long as that `finally` always gets to
    run. It does not if the whole worker process is killed (OOM, host
    crash, `kill -9`) the instant a job finishes: the next job to reuse
    this process (a non-forking worker, or the scheduler's own long-lived
    loop) then inherits connections bound to a loop that no longer exists,
    and crashes on its very first DB checkout - before this fix, with
    exactly the reported MissingGreenlet/"attached to a different loop"
    error.

    Simulated here by calling the inner coroutine directly, which bypasses
    `_run_and_dispose`'s own cleanup entirely - modelling a run whose
    disposal never got the chance to happen.
    """

    async def _run_once_with_lost_cleanup() -> None:
        await tasks.engine.dispose()  # starts clean, like a freshly forked process
        await tasks._poll_mailbox_async(committed_mailbox)
        # Deliberately no dispose here - the process gets killed right now,
        # before cleanup, in the scenario this test models.

    # A separate OS thread running its own asyncio.run() - the same
    # relationship one job/scheduler tick has to the next.
    await asyncio.to_thread(lambda: asyncio.run(_run_once_with_lost_cleanup()))

    result = await asyncio.to_thread(tasks.poll_mailbox_job, committed_mailbox)
    assert "failed=0" in result
