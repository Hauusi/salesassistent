from __future__ import annotations

import enum
import uuid
from datetime import datetime

from app.schemas.common import ORMBase
from app.schemas.email import EmailSummaryOut


class ContactLastStatus(str, enum.Enum):
    """The three-value status a contact overview needs - derived at
    request time, never persisted (see routes/contacts.py:_last_status).

    It combines two things that are stored separately: EmailStatus (was the
    latest inquiry answered?) and CaseStatus (was its case closed?).
    """

    OFFEN = "offen"
    BEANTWORTET = "beantwortet"
    ABGESCHLOSSEN = "abgeschlossen"


class ContactListItemOut(ORMBase):
    # No defaults on these: the route always populates them, so declaring
    # them required is the accurate contract (see CaseListItemOut for the
    # same reasoning).
    id: uuid.UUID
    name: str | None
    email_address: str
    company: str | None
    total_inquiries: int
    last_status: ContactLastStatus | None
    last_contact_at: datetime | None
    needs_followup: bool


class ContactDetailOut(ContactListItemOut):
    # Chronological (newest first, matching CaseDetailOut.emails), so the
    # detail view reads as the contact's history at a glance.
    emails: list[EmailSummaryOut]
