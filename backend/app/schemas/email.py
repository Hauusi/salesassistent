from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.common import CaseOut, ContactOut


class EmailOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    subject: str | None
    sender_address: str
    sender_name: str | None
    raw_content: str
    snippet: str | None
    received_at: datetime
    wichtigkeits_kategorie: str | None
    typ: str
    status: str
    classification_confidence: float | None
    classification_reasoning: str | None
    contact: ContactOut | None
    case: CaseOut | None
