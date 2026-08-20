from __future__ import annotations

import uuid
from datetime import datetime

from app.models.enums import CaseStatus
from app.schemas.common import ContactOut, ORMBase
from app.schemas.email import EmailOut


class CaseListItemOut(ORMBase):
    # No defaults on these: the route always populates them, so declaring
    # them required is the accurate contract. A default would render them
    # as optional in the OpenAPI document, which propagates into the
    # generated frontend types as `T[] | undefined` and forces every call
    # site to guard against a case that cannot occur.
    id: uuid.UUID
    title: str
    summary: str | None
    status: CaseStatus
    created_at: datetime
    contacts: list[ContactOut]
    email_count: int


class CaseDetailOut(CaseListItemOut):
    emails: list[EmailOut]
