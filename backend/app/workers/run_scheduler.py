"""Polling scheduler process entrypoint: `python -m app.workers.run_scheduler`.

Every MAIL_POLL_INTERVAL_SECONDS, enqueues one Gmail-poll job per active
mailbox onto the RQ queue. Deliberately simple (a sleep loop) - sufficient
for the MVP's polling approach; a Gmail Pub/Sub push subscription would
replace this in a later stage (see README).
"""
from __future__ import annotations

import logging
import time

from app.config import get_settings
from app.workers.tasks import enqueue_poll_for_all_active_mailboxes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()


def main() -> None:
    logger.info("Scheduler started, polling every %ss", settings.mail_poll_interval_seconds)
    while True:
        try:
            count = enqueue_poll_for_all_active_mailboxes()
            logger.info("Enqueued poll jobs for %s mailbox(es)", count)
        except Exception:
            logger.exception("Scheduler tick failed")
        time.sleep(settings.mail_poll_interval_seconds)


if __name__ == "__main__":
    main()
