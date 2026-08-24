"""End-to-end regression test: a mailbox with one new mail must be
classified exactly once, even when two triggers race to poll it at the
same time (a scheduler tick colliding with a "poll now" click, or two
scheduler replicas).

Before this fix, `enqueue_poll`'s is-it-in-flight check and the enqueue
itself were two separate Redis round-trips (see app/workers/queue.py).
Two racing callers could both see "not in flight" and both enqueue a poll
job for the same mailbox; both work horses would then fetch the same new
message and both call classify_email on it, spending Claude/Voyage tokens
twice before one of them lost the uq_email_mailbox_gmail_id race at
commit. This test drives the real entrypoints - enqueue_poll, RQ, and
poll_mailbox_job - rather than asserting on the queue's internal state, so
it catches a regression regardless of which layer it creeps back in at.
"""
from __future__ import annotations

import asyncio
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from rq import SimpleWorker
from rq.timeouts import BaseDeathPenalty
from sqlalchemy import select

from app.db import async_session_factory
from app.models.email_message import EmailMessage
from app.models.enums import TypKategorie, WichtigkeitsKategorie
from app.models.mailbox import Mailbox
from app.models.tenant import Tenant
from app.models.user import User
from app.services import gmail_client, pipeline
from app.services.classification import ClassificationResult
from app.workers import queue as queue_module


class _NoDeathPenalty(BaseDeathPenalty):
    """RQ's default per-job timeout enforcement uses SIGALRM, which only
    works in the main thread - irrelevant here anyway (a burst SimpleWorker
    in a test, not a long-lived process). Runs the job with no timeout
    enforcement instead of installing a signal handler from a background
    thread."""

    def setup_death_penalty(self) -> None:
        pass

    def cancel_death_penalty(self) -> None:
        pass


class _FakeCredentials:
    token = None
    refresh_token = None
    expiry = None
    scopes = None


@pytest.fixture(autouse=True)
def _stub_boundaries(monkeypatch):
    """Same boundary stubs as test_poll_mailbox_job_e2e.py, plus a call
    counter on classify_email - the thing under test here."""

    async def _get_gmail_service(_mailbox):
        return object(), _FakeCredentials()

    async def _list_new_message_ids(_service, after_query=None, max_results=25):
        return ["gm-once-1"]

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

    calls: list[str] = []

    async def _counting_classify(**_kwargs) -> ClassificationResult:
        calls.append(_kwargs.get("body", ""))
        return ClassificationResult(
            wichtigkeits_kategorie=WichtigkeitsKategorie.INFORMATION,
            typ=TypKategorie.KEINER,
            confidence=0.9,
            reasoning="Test",
        )

    async def _embed(*_args, **_kwargs) -> list[float]:
        return [0.1] * 1024

    monkeypatch.setattr(gmail_client, "get_gmail_service", _get_gmail_service)
    monkeypatch.setattr(gmail_client, "list_new_message_ids", _list_new_message_ids)
    monkeypatch.setattr(gmail_client, "get_message", _get_message)
    monkeypatch.setattr(pipeline.classification, "classify_email", _counting_classify)
    monkeypatch.setattr(pipeline.embeddings, "embed_text", _embed)

    return calls


@pytest.fixture
async def committed_mailbox(_schema) -> str:
    """A mailbox actually committed to Postgres - see
    test_poll_mailbox_job_e2e.py::committed_mailbox for why this can't be
    the SAVEPOINT-scoped `db_session` fixture."""
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


@pytest.fixture
def clean_queue():
    from redis.exceptions import ConnectionError as RedisConnectionError

    try:
        queue_module.get_redis().ping()
    except (RedisConnectionError, OSError):  # pragma: no cover - CI without Redis
        pytest.skip("Redis nicht erreichbar")

    q = queue_module.get_queue()
    q.empty()
    yield q
    q.empty()


async def test_racing_poll_triggers_classify_the_mail_exactly_once(
    committed_mailbox: str, clean_queue, monkeypatch, _stub_boundaries
) -> None:
    calls = _stub_boundaries
    mailbox_id = committed_mailbox

    # Widen the enqueue's check-then-enqueue window (see
    # test_queue_dedup.py for why this is needed for a fair regression
    # test rather than a rarely-reproducing one) without changing which
    # code path runs: the fix's lock is acquired *before* this fetch, so a
    # thread that loses the lock never reaches the slowed-down call at all.
    original_fetch = queue_module.Job.fetch

    def _slow_fetch(*args, **kwargs):
        try:
            return original_fetch(*args, **kwargs)
        finally:
            time.sleep(0.05)

    monkeypatch.setattr(queue_module.Job, "fetch", _slow_fetch)

    barrier = threading.Barrier(2)
    enqueue_results: list[object] = [None, None]

    def _trigger(index: int) -> None:
        barrier.wait(timeout=5)
        enqueue_results[index] = queue_module.enqueue_poll(mailbox_id)

    # Simulates a scheduler tick colliding with a manual "poll now" click -
    # both call the same real enqueue_poll() entrypoint at once.
    threads = [threading.Thread(target=_trigger, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert sum(1 for r in enqueue_results if r is not None) == 1, (
        "both racing triggers enqueued a poll for the same mailbox"
    )
    assert clean_queue.count == 1

    # Actually run whatever ended up queued - a SimpleWorker executes jobs
    # in-process (no fork), which is what lets this reach into the same
    # monkeypatched boundaries/counters set up above. Runs on a background
    # thread (asyncio.to_thread) so worker.work()'s asyncio.run() inside
    # poll_mailbox_job doesn't collide with this test's own running loop -
    # which also means it isn't the main thread, so RQ's default SIGINT/
    # SIGTERM handler installation (irrelevant for a burst worker in a
    # test) has to be skipped rather than attempted.
    worker = SimpleWorker([clean_queue], connection=queue_module.get_redis())
    monkeypatch.setattr(worker, "_install_signal_handlers", lambda: None)
    monkeypatch.setattr(worker, "death_penalty_class", _NoDeathPenalty)
    await asyncio.to_thread(worker.work, burst=True)

    assert len(calls) == 1, f"classify_email was called {len(calls)} times for one new mail"

    async with async_session_factory() as db:
        stored = (
            await db.execute(
                select(EmailMessage.gmail_message_id).where(
                    EmailMessage.mailbox_id == uuid.UUID(mailbox_id)
                )
            )
        ).scalars().all()
    assert stored == ["gm-once-1"]
