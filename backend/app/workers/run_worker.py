"""RQ worker process entrypoint: `python -m app.workers.run_worker`."""
from __future__ import annotations

import logging

from rq import Worker

from app.workers.queue import get_queue, get_redis

logging.basicConfig(level=logging.INFO)


def main() -> None:
    worker = Worker([get_queue()], connection=get_redis())
    worker.work()


if __name__ == "__main__":
    main()
