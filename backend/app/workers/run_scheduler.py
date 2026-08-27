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
from app.logging_config import configure_logging
from app.services import startup_checks
from app.workers.tasks import enqueue_poll_for_all_active_mailboxes

configure_logging()
logger = logging.getLogger(__name__)


def main() -> None:
    # Unlike the API process, this loop has no per-request boundary where a
    # missing Anthropic/Voyage/Google credential would surface on its own -
    # every tick would just fail identically and log it, forever. Checked
    # once, hard, before the first tick - see
    # startup_checks.verify_required_settings.
    startup_checks.verify_required_settings()

    settings = get_settings()
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
