from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.common import CaseOut, ContactOut


class EmailSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    subject: str | None
    sender_address: str
    sender_name: str | None
    snippet: str | None
    received_at: datetime
    wichtigkeits_kategorie: str | None
    typ: str
    status: str
    contact: ContactOut | None
    case: CaseOut | None


class DraftOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email_message_id: uuid.UUID
    subject: str | None
    body: str
    status: str
    rag_context_summary: str | None
    created_at: datetime
    updated_at: datetime
    sent_at: datetime | None
    email_message: EmailSummaryOut


class DraftUpdateIn(BaseModel):
    subject: str | None = None
    body: str


class DraftRejectIn(BaseModel):
    reason: str | None = None
