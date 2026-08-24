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

That dedup key must not become a one-way lock, though. RQ's own
`job_timeout` enforcement depends on the *parent* worker process staying
alive to monitor the work horse it forked for the job; if the whole worker
process dies instead (OOM kill, host crash, `kill -9`) nobody is left to
ever mark the job finished. The job's hash then sits in Redis with status
"started" forever, and every future poll of that mailbox is skipped as
"already in flight" - permanently, until someone deletes the Redis key by
hand. `is_poll_in_flight` therefore treats a "started" job as stale (and
cleans it up) once it has run well past its own timeout, rather than
trusting RQ's bookkeeping unconditionally.

The dedup *check* also has to be atomic with the enqueue, not just
present. `is_poll_in_flight()` (a Redis read) and `queue.enqueue()` (a
Redis write) are two separate round-trips; RQ's `enqueue_job()` pushes the
job id onto the queue's list unconditionally; it never checks whether that
id is already queued or running. Two callers that both call `enqueue_poll`
for the same mailbox close enough together - a scheduler tick racing a
"poll now" click, or two scheduler replicas - can each see "not in flight"
before either has written anything, and each then pushes its own copy of
the same job id: two work horses run `poll_mailbox_job` concurrently, both
read the same not-yet-advanced watermark, and both spend Claude/Voyage
tokens classifying the same new mail before one of them loses the
uq_email_mailbox_gmail_id race at commit. `enqueue_poll` therefore wraps
the check-then-enqueue in a short-lived Redis lock (`SET NX`), which is
atomic server-side regardless of how many callers race it.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

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

# On top of the job's own job_timeout: give a live worker's own timeout
# enforcement a chance to fire first, so this is a last-resort self-heal
# for a *worker* that died, not a race against a *job* that is merely
# about to be killed for running too long.
_STALE_STARTED_GRACE_SECONDS = 60

# Just long enough to cover one Redis status read plus one enqueue write -
# not a lock on the poll itself (that's the job's own job_timeout). A TTL
# instead of an unconditional release means a caller that dies mid-check
# (rare, but the whole point of not trusting bookkeeping blindly elsewhere
# in this module) can't wedge every future enqueue for this mailbox.
_ENQUEUE_LOCK_TTL_SECONDS = 10

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


def _is_stale_started_job(job: Job) -> bool:
    """True once a "started" job has run well past its own job_timeout -
    a sign the worker that was running it is gone, not that the job is
    merely slow."""
    if job.started_at is None:
        return False
    timeout = job.timeout or get_settings().mail_poll_job_timeout_seconds
    deadline = job.started_at + timedelta(seconds=timeout + _STALE_STARTED_GRACE_SECONDS)
    # RQ stores its own timestamps as naive UTC (datetime.utcnow()); matching
    # that here avoids a naive/aware comparison error.
    return datetime.utcnow() > deadline


def is_poll_in_flight(mailbox_id: str) -> bool:
    try:
        job = Job.fetch(poll_job_id(mailbox_id), connection=get_redis())
    except NoSuchJobError:
        return False

    status = job.get_status(refresh=False)
    if status not in _IN_FLIGHT:
        return False

    if status == "started" and _is_stale_started_job(job):
        logger.warning(
            "poll_job_stale_cleanup mailbox=%s job=%s started_at=%s",
            mailbox_id, job.id, job.started_at,
        )
        job.delete()
        return False

    return True


def _enqueue_lock_key(mailbox_id: str) -> str:
    return f"poll-enqueue-lock:{mailbox_id}"


def enqueue_poll(mailbox_id: str) -> Job | None:
    """Enqueues a poll for one mailbox, or returns None if one is already
    in flight.

    The is-it-in-flight check and the enqueue itself are wrapped in a
    short-lived `SET NX` lock so two concurrent callers can't both pass the
    check before either has enqueued - see the module docstring for why
    that race matters (it double-spends Claude/Voyage tokens on the same
    mail, not just a redundant job).
    """
    lock_key = _enqueue_lock_key(mailbox_id)
    if not get_redis().set(lock_key, "1", nx=True, ex=_ENQUEUE_LOCK_TTL_SECONDS):
        # Another caller is deciding right now whether to enqueue this
        # mailbox - don't race it; whichever of us loses this round, the
        # mailbox is (about to be) covered either way.
        logger.info("poll_skipped_concurrent_enqueue mailbox=%s", mailbox_id)
        return None

    try:
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
    finally:
        get_redis().delete(lock_key)
