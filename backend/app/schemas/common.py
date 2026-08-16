from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ORMBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ContactOut(ORMBase):
    id: uuid.UUID
    email_address: str
    name: str | None
    company: str | None


class CaseOut(ORMBase):
    id: uuid.UUID
    title: str
    summary: str | None
    status: str
    created_at: datetime
