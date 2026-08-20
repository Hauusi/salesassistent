from __future__ import annotations

import uuid
from datetime import datetime

from app.models.enums import CaseStatus
from app.schemas.common import ContactOut, ORMBase
from app.schemas.email import EmailOut


class CaseListItemOut(ORMBase):
    id: uuid.UUID
    title: str
    summary: str | None
    status: CaseStatus
    created_at: datetime
    contacts: list[ContactOut] = []
    email_count: int = 0


class CaseDetailOut(CaseListItemOut):
    emails: list[EmailOut] = []
