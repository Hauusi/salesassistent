"""RQ worker process entrypoint: `python -m app.workers.run_worker`."""
from __future__ import annotations

from rq import Worker

from app.logging_config import configure_logging
from app.services import startup_checks
from app.workers.queue import get_queue, get_redis

configure_logging()


def main() -> None:
    # Every job would otherwise fail identically the first time it touches
    # Gmail, Claude, Voyage or the encrypted token store, and RQ's own
    # retry policy (see app/workers/queue.py) would just re-run the same
    # doomed job a few times before giving up - a slow, log-buried way to
    # discover a missing credential. Checked once, hard, before this
    # process claims any job - see startup_checks.verify_required_settings.
    startup_checks.verify_required_settings()

    worker = Worker([get_queue()], connection=get_redis())
    worker.work()


if __name__ == "__main__":
    main()
