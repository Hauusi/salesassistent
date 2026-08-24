"""The scheduler must not stack polls on a mailbox that is still busy.

A poll cycle is (Gmail + Claude + Voyage) per mail, sequentially, so it
routinely outlives the scheduler's fixed tick. Two concurrent polls on one
mailbox both miss the idempotency check in process_incoming_email, both
classify the same mail, and the loser hits uq_email_mailbox_gmail_id at
commit: a failed job plus Claude tokens paid twice.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from rq.job import JobStatus

from app.workers import queue as queue_module


@pytest.fixture
def clean_queue():
    """Uses the real Redis when one is reachable; skips otherwise."""
    try:
        queue_module.get_redis().ping()
    except (RedisConnectionError, OSError):  # pragma: no cover - CI without Redis
        pytest.skip("Redis nicht erreichbar")

    q = queue_module.get_queue()
    q.empty()
    yield q
    q.empty()


def test_poll_job_id_is_stable_per_mailbox() -> None:
    mailbox_id = str(uuid.uuid4())
    assert queue_module.poll_job_id(mailbox_id) == queue_module.poll_job_id(mailbox_id)
    assert queue_module.poll_job_id(mailbox_id) != queue_module.poll_job_id(str(uuid.uuid4()))


def test_enqueue_poll_returns_a_job_for_an_idle_mailbox(clean_queue) -> None:
    mailbox_id = str(uuid.uuid4())
    job = queue_module.enqueue_poll(mailbox_id)

    assert job is not None
    assert job.id == queue_module.poll_job_id(mailbox_id)


def test_enqueue_poll_skips_a_mailbox_whose_poll_is_still_queued(clean_queue) -> None:
    mailbox_id = str(uuid.uuid4())

    first = queue_module.enqueue_poll(mailbox_id)
    second = queue_module.enqueue_poll(mailbox_id)

    assert first is not None
    assert second is None, "a second poll was stacked on a mailbox that is still busy"
    assert clean_queue.count == 1


def test_enqueue_poll_allows_a_different_mailbox_meanwhile(clean_queue) -> None:
    busy = str(uuid.uuid4())
    other = str(uuid.uuid4())

    queue_module.enqueue_poll(busy)
    assert queue_module.enqueue_poll(other) is not None
    assert clean_queue.count == 2


def test_enqueue_poll_is_possible_again_once_the_job_is_gone(clean_queue) -> None:
    mailbox_id = str(uuid.uuid4())
    first = queue_module.enqueue_poll(mailbox_id)
    first.delete()

    assert queue_module.enqueue_poll(mailbox_id) is not None


def _mark_started(job, *, started_at) -> None:
    """Simulates a job the work horse picked up but never finished, without
    actually running one - the crash scenario under test is precisely that
    nothing ever gets the chance to move the job past "started"."""
    job.set_status(JobStatus.STARTED)
    job.started_at = started_at
    job.save()


def test_a_hung_started_job_past_its_timeout_no_longer_blocks_polling(clean_queue) -> None:
    """Regression: if the worker process itself dies (OOM, host crash,
    kill -9) - not just the job - nobody is left to ever mark the job
    failed. Before this fix, the mailbox's poll stayed 'already in flight'
    in Redis forever, and no future tick would ever enqueue it again."""
    mailbox_id = str(uuid.uuid4())
    job = queue_module.enqueue_poll(mailbox_id)
    assert job is not None

    ancient = datetime.utcnow() - timedelta(
        seconds=job.timeout + queue_module._STALE_STARTED_GRACE_SECONDS + 1
    )
    _mark_started(job, started_at=ancient)

    assert queue_module.is_poll_in_flight(mailbox_id) is False
    # Cleaned up, not just ignored - it must not keep silently piling up.
    assert queue_module.Job.exists(job.id, connection=queue_module.get_redis()) is False

    again = queue_module.enqueue_poll(mailbox_id)
    assert again is not None, "a stale started job must not block re-enqueueing forever"


def test_a_recently_started_job_still_counts_as_in_flight(clean_queue) -> None:
    """A job that is merely slow - not stuck - must still be respected;
    only staleness well past its own timeout should trigger cleanup."""
    mailbox_id = str(uuid.uuid4())
    job = queue_module.enqueue_poll(mailbox_id)
    assert job is not None

    _mark_started(job, started_at=datetime.utcnow())

    assert queue_module.is_poll_in_flight(mailbox_id) is True
    assert queue_module.enqueue_poll(mailbox_id) is None


def test_enqueued_poll_carries_a_retry_policy(clean_queue) -> None:
    """Infrastructure failures (worker loss, Redis blip) deserve a retry;
    per-message failures are absorbed inside the job and never get here."""
    job = queue_module.enqueue_poll(str(uuid.uuid4()))
    assert job.retries_left is not None and job.retries_left > 0


def test_the_poll_job_path_resolves() -> None:
    """The job is enqueued by dotted path to break a queue<->tasks import
    cycle, which means a rename would only fail inside the worker. Assert
    it here instead."""
    import importlib

    module_name, _, function_name = queue_module.POLL_JOB_PATH.rpartition(".")
    module = importlib.import_module(module_name)
    assert callable(getattr(module, function_name))
