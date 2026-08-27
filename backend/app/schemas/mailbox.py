from __future__ import annotations

import uuid
from datetime import datetime

from app.models.enums import MailboxPollStatus
from app.schemas.common import ORMBase


class MailboxOut(ORMBase):
    id: uuid.UUID
    email_address: str
    is_active: bool
    last_synced_at: datetime | None
    last_poll_status: MailboxPollStatus | None
    last_poll_error_message: str | None
    last_poll_at: datetime | None
    created_at: datetime


class PollTriggerOut(ORMBase):
    """Result of a manual poll trigger.

    `job_id` is null when a poll for this mailbox was already in flight -
    see app/workers/queue.py for why a second one is not stacked.
    """

    job_id: str | None
    status: str
