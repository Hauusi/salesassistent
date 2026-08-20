from __future__ import annotations

import uuid
from datetime import datetime

from app.models.enums import EmailStatus, TypKategorie, WichtigkeitsKategorie
from app.schemas.common import CaseOut, ContactOut, ORMBase


class EmailSummaryOut(ORMBase):
    """Everything a list view needs - deliberately without `raw_content`,
    which is the largest field by far and never rendered in a list."""

    id: uuid.UUID
    subject: str | None
    sender_address: str
    sender_name: str | None
    snippet: str | None
    received_at: datetime
    # Typed as the enums rather than `str`: the values are a closed set, and
    # declaring them as such is what lets the generated frontend types be a
    # union instead of `string` (see frontend/lib/api-schema.ts).
    wichtigkeits_kategorie: WichtigkeitsKategorie | None
    typ: TypKategorie
    status: EmailStatus
    contact: ContactOut | None
    case: CaseOut | None


class EmailOut(EmailSummaryOut):
    raw_content: str
    classification_confidence: float | None
    classification_reasoning: str | None
