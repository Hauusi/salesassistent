"""The scheduler must not stack polls on a mailbox that is still busy.

A poll cycle is (Gmail + Claude + Voyage) per mail, sequentially, so it
routinely outlives the scheduler's fixed tick. Two concurrent polls on one
mailbox both miss the idempotency check in process_incoming_email, both
classify the same mail, and the loser hits uq_email_mailbox_gmail_id at
commit: a failed job plus Claude tokens paid twice.
"""
from __future__ import annotations

import threading
import time
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


def test_concurrent_enqueue_poll_for_the_same_mailbox_only_enqueues_once(
    clean_queue, monkeypatch
) -> None:
    """Regression for the exact cost bug: is_poll_in_flight (a Redis read)
    and the enqueue itself (a Redis write) are two separate round-trips.
    Two callers racing enqueue_poll for the same mailbox - a scheduler tick
    colliding with a "poll now" click, or two scheduler replicas - could
    both see "not in flight" before either had written anything, and RQ's
    enqueue() pushes the job id onto the queue's list unconditionally, so
    each caller pushed its own copy: two work horses run the same poll
    concurrently and spend Claude/Voyage tokens classifying the same new
    mail twice, before one of them loses the uq_email_mailbox_gmail_id
    race at commit.

    The barrier makes both threads call enqueue_poll() at (as close to) the
    same instant as two threads can - without it the race still exists,
    but one thread easily wins the lock microseconds before the other even
    starts, and the test would pass by luck rather than by the lock
    actually serializing them.
    """
    mailbox_id = str(uuid.uuid4())
    barrier = threading.Barrier(2)

    # Widens the is-it-in-flight check itself, so both threads are
    # guaranteed to still be inside it when the other one runs - without
    # this the race is real but narrow (a fetch against local Redis is
    # fast enough that one thread usually finishes its whole enqueue
    # before the other's check completes, so the bug reproduces only
    # rarely on the unfixed code - not a fair regression test). The fix
    # acquires its lock *before* this check, so the delayed thread on the
    # fixed code path is the one that never got the lock and returns
    # immediately, without ever calling the slowed-down fetch at all.
    original_fetch = queue_module.Job.fetch

    def _slow_fetch(*args, **kwargs):
        try:
            return original_fetch(*args, **kwargs)
        finally:
            time.sleep(0.05)

    monkeypatch.setattr(queue_module.Job, "fetch", _slow_fetch)

    results: list[object] = [None, None]

    def _call(index: int) -> None:
        # Both threads reach the real enqueue_poll() call at (as close to)
        # the same instant as two threads can - this is what actually
        # exercises the race: without the barrier, one thread easily wins
        # the lock microseconds before the other even starts.
        barrier.wait(timeout=5)
        results[index] = queue_module.enqueue_poll(mailbox_id)

    threads = [threading.Thread(target=_call, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    enqueued = [r for r in results if r is not None]
    assert len(enqueued) == 1, "both concurrent callers enqueued a poll for the same mailbox"
    assert clean_queue.count == 1


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
