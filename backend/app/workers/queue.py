"""RQ queue wiring, plus the enqueue policy for mailbox polling.

The scheduler ticks on a fixed interval regardless of how long a poll
takes. Enqueueing blindly meant that a poll running longer than one tick -
routine, since a batch is (Gmail + Claude + Voyage) per mail, sequentially
- got a second job stacked on the same mailbox. Both then raced the
idempotency check in process_incoming_email, both classified the same
mail, and the loser hit the uq_email_mailbox_gmail_id constraint at commit
time: a failed job plus Claude tokens paid twice.

`enqueue_poll` therefore keys each job to its mailbox and skips a mailbox
whose previous poll has not finished.
"""
from __future__ import annotations

import logging

from redis import Redis
from rq import Queue, Retry
from rq.exceptions import NoSuchJobError
from rq.job import Job

from app.config import get_settings

logger = logging.getLogger(__name__)

QUEUE_NAME = "mail_processing"
POLL_JOB_PATH = "app.workers.tasks.poll_mailbox_job"

# Job states that mean "this mailbox already has a poll in flight".
_IN_FLIGHT = frozenset({"queued", "started", "deferred", "scheduled"})

_redis_conn: Redis | None = None
_queue: Queue | None = None


def get_redis() -> Redis:
    global _redis_conn
    if _redis_conn is None:
        _redis_conn = Redis.from_url(get_settings().redis_url)
    return _redis_conn


def get_queue() -> Queue:
    global _queue
    if _queue is None:
        _queue = Queue(QUEUE_NAME, connection=get_redis())
    return _queue


def poll_job_id(mailbox_id: str) -> str:
    """Deterministic job id, so a mailbox's poll is identifiable across
    ticks without a separate registry."""
    return f"poll-mailbox-{mailbox_id}"


def is_poll_in_flight(mailbox_id: str) -> bool:
    try:
        job = Job.fetch(poll_job_id(mailbox_id), connection=get_redis())
    except NoSuchJobError:
        return False
    return job.get_status(refresh=False) in _IN_FLIGHT


def enqueue_poll(mailbox_id: str) -> Job | None:
    """Enqueues a poll for one mailbox, or returns None if one is already
    in flight."""
    if is_poll_in_flight(mailbox_id):
        logger.info("poll_skipped_already_in_flight mailbox=%s", mailbox_id)
        return None

    settings = get_settings()
    return get_queue().enqueue(
        # Referenced by dotted path rather than imported: queue and tasks
        # would otherwise import each other, which is what forced both
        # modules into function-local imports before.
        POLL_JOB_PATH,
        mailbox_id,
        job_id=poll_job_id(mailbox_id),
        job_timeout=settings.mail_poll_job_timeout_seconds,
        # A poll that dies on infrastructure (Redis blip, worker loss) is
        # worth retrying; per-message failures are already absorbed inside
        # the job itself and never reach this level.
        retry=Retry(max=3, interval=[30, 120, 300]),
        # Keep a finished job's record around long enough that the next
        # tick can still see a *running* one, but not so long that a
        # finished poll blocks the following tick.
        result_ttl=60,
        failure_ttl=86400,
    )
