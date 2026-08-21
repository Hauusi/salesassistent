from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_tenant
from app.config import get_settings
from app.db import get_db
from app.models.contact import Contact
from app.models.email_message import EmailMessage
from app.models.enums import CaseStatus, EmailStatus
from app.models.tenant import Tenant
from app.schemas.contact import ContactDetailOut, ContactLastStatus, ContactListItemOut

router = APIRouter(prefix="/api/contacts", tags=["contacts"])


def _last_status(latest_email: EmailMessage | None) -> ContactLastStatus | None:
    """Derives the tri-state overview status from the contact's single
    most recent email.

    A closed case (CaseStatus.GESCHLOSSEN) outranks the email's own status:
    a thread can be marked done even after the final reply went out, so
    that is checked first. Otherwise EmailStatus.ERLEDIGT - set when a
    draft is approved and sent, see routes/drafts.py:approve_draft - means
    the inquiry was answered. Anything else (NEU, WARTET_AUF_FREIGABE,
    ABGELEGT, AUSGEBLENDET) still counts as open: none of those mean a
    reply went out.
    """
    if latest_email is None:
        return None
    if latest_email.case is not None and latest_email.case.status == CaseStatus.GESCHLOSSEN:
        return ContactLastStatus.ABGESCHLOSSEN
    if latest_email.status == EmailStatus.ERLEDIGT:
        return ContactLastStatus.BEANTWORTET
    return ContactLastStatus.OFFEN


def _needs_followup(
    status: ContactLastStatus | None, last_contact_at: datetime | None, cutoff: datetime
) -> bool:
    return status == ContactLastStatus.OFFEN and last_contact_at is not None and last_contact_at < cutoff


def _followup_cutoff(followup_days: int | None) -> datetime:
    days = followup_days if followup_days is not None else get_settings().contact_followup_threshold_days
    return datetime.now(UTC) - timedelta(days=days)


async def _latest_email_by_contact(db: AsyncSession, tenant: Tenant) -> dict[uuid.UUID, EmailMessage]:
    """One EmailMessage per contact: the most recently received one,
    including its case (needed for _last_status). DISTINCT ON keeps this
    to a single query instead of one per contact."""
    result = await db.execute(
        select(EmailMessage)
        .distinct(EmailMessage.contact_id)
        .where(EmailMessage.tenant_id == tenant.id, EmailMessage.contact_id.isnot(None))
        .options(selectinload(EmailMessage.case))
        .order_by(EmailMessage.contact_id, EmailMessage.received_at.desc())
    )
    return {email.contact_id: email for email in result.scalars().all()}


@router.get("", response_model=list[ContactListItemOut])
async def list_contacts(
    followup_days: int | None = Query(
        None,
        ge=0,
        description="Overrides the follow-up threshold (days) for this request only. "
        "Defaults to the configured contact_followup_threshold_days setting.",
    ),
    db: AsyncSession = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> list[ContactListItemOut]:
    cutoff = _followup_cutoff(followup_days)

    contacts_result = await db.execute(
        select(Contact).where(Contact.tenant_id == tenant.id)
    )
    contacts = list(contacts_result.scalars().all())

    counts_result = await db.execute(
        select(EmailMessage.contact_id, func.count(EmailMessage.id))
        .where(EmailMessage.tenant_id == tenant.id, EmailMessage.contact_id.isnot(None))
        .group_by(EmailMessage.contact_id)
    )
    counts = dict(counts_result.all())

    latest_by_contact = await _latest_email_by_contact(db, tenant)

    items = []
    for contact in contacts:
        latest = latest_by_contact.get(contact.id)
        status = _last_status(latest)
        last_contact_at = latest.received_at if latest else None
        items.append(
            ContactListItemOut(
                id=contact.id,
                name=contact.name,
                email_address=contact.email_address,
                company=contact.company,
                total_inquiries=counts.get(contact.id, 0),
                last_status=status,
                last_contact_at=last_contact_at,
                needs_followup=_needs_followup(status, last_contact_at, cutoff),
            )
        )

    # Newest contact first by default; the frontend table re-sorts
    # client-side (small dataset, one sortable column).
    items.sort(key=lambda c: c.last_contact_at or datetime.min.replace(tzinfo=UTC), reverse=True)
    return items


@router.get("/{contact_id}", response_model=ContactDetailOut)
async def get_contact(
    contact_id: uuid.UUID,
    followup_days: int | None = Query(None, ge=0),
    db: AsyncSession = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> ContactDetailOut:
    result = await db.execute(
        select(Contact).where(Contact.id == contact_id, Contact.tenant_id == tenant.id)
    )
    contact = result.scalar_one_or_none()
    if contact is None:
        raise HTTPException(status_code=404, detail="Kontakt nicht gefunden.")

    emails_result = await db.execute(
        select(EmailMessage)
        .where(EmailMessage.contact_id == contact.id, EmailMessage.tenant_id == tenant.id)
        .options(selectinload(EmailMessage.case), selectinload(EmailMessage.contact))
        .order_by(EmailMessage.received_at.desc())
    )
    emails = list(emails_result.scalars().all())

    latest = emails[0] if emails else None
    status = _last_status(latest)
    last_contact_at = latest.received_at if latest else None
    cutoff = _followup_cutoff(followup_days)

    return ContactDetailOut(
        id=contact.id,
        name=contact.name,
        email_address=contact.email_address,
        company=contact.company,
        total_inquiries=len(emails),
        last_status=status,
        last_contact_at=last_contact_at,
        needs_followup=_needs_followup(status, last_contact_at, cutoff),
        emails=emails,
    )
