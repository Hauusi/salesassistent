"""RQ worker process entrypoint: `python -m app.workers.run_worker`."""
from __future__ import annotations

from rq import Worker

from app.logging_config import configure_logging
from app.workers.queue import get_queue, get_redis

configure_logging()


def main() -> None:
    worker = Worker([get_queue()], connection=get_redis())
    worker.work()


if __name__ == "__main__":
    main()
