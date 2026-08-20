"""The scheduler must not stack polls on a mailbox that is still busy.

A poll cycle is (Gmail + Claude + Voyage) per mail, sequentially, so it
routinely outlives the scheduler's fixed tick. Two concurrent polls on one
mailbox both miss the idempotency check in process_incoming_email, both
classify the same mail, and the loser hits uq_email_mailbox_gmail_id at
commit: a failed job plus Claude tokens paid twice.
"""
from __future__ import annotations

import uuid

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

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


def test_enqueued_poll_carries_a_retry_policy(clean_queue) -> None:
    """Infrastructure failures (worker loss, Redis blip) deserve a retry;
    per-message failures are absorbed inside the job and never get here."""
    job = queue_module.enqueue_poll(str(uuid.uuid4()))
    assert job.retries_left is not None and job.retries_left > 0
