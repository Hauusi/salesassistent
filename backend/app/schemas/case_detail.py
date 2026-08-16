from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.common import ContactOut
from app.schemas.email import EmailOut


class CaseListItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    summary: str | None
    status: str
    created_at: datetime
    contacts: list[ContactOut] = []
    email_count: int = 0


class CaseDetailOut(CaseListItemOut):
    emails: list[EmailOut] = []
