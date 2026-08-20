from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import DraftStatus
from app.schemas.common import ORMBase
from app.schemas.email import EmailSummaryOut


class DraftOut(ORMBase):
    id: uuid.UUID
    email_message_id: uuid.UUID
    subject: str | None
    body: str
    status: DraftStatus
    rag_context_summary: str | None
    created_at: datetime
    updated_at: datetime
    sent_at: datetime | None
    email_message: EmailSummaryOut


class DraftUpdateIn(BaseModel):
    subject: str | None = Field(default=None, max_length=998)
    body: str


class DraftRejectIn(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)
