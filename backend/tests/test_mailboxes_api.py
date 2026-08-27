"""Tests for mailbox management, in particular deletion - previously only
possible via direct SQL (see app/services/mailbox_cleanup.py).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.attachment import Attachment
from app.models.case import Case, CaseContact
from app.models.contact import Contact
from app.models.draft import Draft
from app.models.email_message import EmailMessage
from app.models.enums import EmailStatus, TypKategorie, WichtigkeitsKategorie
from app.models.mailbox import Mailbox
from app.models.product_suggestion import ProductSuggestion
from app.models.tenant import Tenant
from app.models.user import User


async def _make_user(db_session: AsyncSession, tenant: Tenant) -> User:
    user = User(tenant_id=tenant.id, email=f"user-{uuid.uuid4().hex[:8]}@example.com")
    db_session.add(user)
    await db_session.flush()
    return user


async def _make_mailbox(db_session: AsyncSession, tenant: Tenant, user: User) -> Mailbox:
    mailbox = Mailbox(
        tenant_id=tenant.id, user_id=user.id, email_address=f"{uuid.uuid4().hex[:8]}@example.com"
    )
    db_session.add(mailbox)
    await db_session.flush()
    return mailbox


async def _make_email(
    db_session: AsyncSession,
    *,
    tenant: Tenant,
    mailbox: Mailbox,
    contact: Contact | None = None,
    case: Case | None = None,
) -> EmailMessage:
    email = EmailMessage(
        tenant_id=tenant.id,
        mailbox_id=mailbox.id,
        contact_id=contact.id if contact else None,
        case_id=case.id if case else None,
        gmail_message_id=f"gm-{uuid.uuid4().hex}",
        subject="Test",
        sender_address="kunde@example.com",
        raw_content="Hallo",
        wichtigkeits_kategorie=WichtigkeitsKategorie.INFORMATION,
        typ=TypKategorie.KEINER,
        status=EmailStatus.ABGELEGT,
        received_at=datetime.now(UTC),
    )
    db_session.add(email)
    await db_session.flush()
    return email


@pytest.fixture
async def full_mailbox(db_session: AsyncSession, tenant: Tenant):
    """A mailbox with one full stack of dependent rows: an email with a
    draft, an attachment, a product suggestion, plus a contact and a case
    that exist *only* because of this mailbox's mail."""
    user = await _make_user(db_session, tenant)
    mailbox = await _make_mailbox(db_session, tenant, user)

    contact = Contact(tenant_id=tenant.id, email_address="kunde@example.com")
    db_session.add(contact)
    await db_session.flush()

    case = Case(tenant_id=tenant.id, title="Testfall")
    db_session.add(case)
    await db_session.flush()
    db_session.add(CaseContact(case_id=case.id, contact_id=contact.id))
    await db_session.flush()

    email = await _make_email(db_session, tenant=tenant, mailbox=mailbox, contact=contact, case=case)

    db_session.add(Draft(tenant_id=tenant.id, email_message_id=email.id, body="Antwort"))
    db_session.add(
        Attachment(tenant_id=tenant.id, email_message_id=email.id, filename="anhang.pdf")
    )
    db_session.add(
        ProductSuggestion(
            tenant_id=tenant.id,
            email_message_id=email.id,
            sku="SKU-1",
            name="Testprodukt",
            description="Beschreibung",
        )
    )
    await db_session.commit()

    return {"mailbox": mailbox, "contact": contact, "case": case, "email": email}


async def test_delete_mailbox_cascades_email_drafts_attachments_and_suggestions(
    api_client, db_session: AsyncSession, full_mailbox: dict
) -> None:
    mailbox = full_mailbox["mailbox"]
    email_id = full_mailbox["email"].id

    response = await api_client.delete(f"/api/mailboxes/{mailbox.id}")
    assert response.status_code == 204

    assert (await db_session.get(Mailbox, mailbox.id)) is None
    assert (await db_session.get(EmailMessage, email_id)) is None

    drafts = (
        await db_session.execute(select(Draft).where(Draft.email_message_id == email_id))
    ).scalars().all()
    assert drafts == []

    attachments = (
        await db_session.execute(select(Attachment).where(Attachment.email_message_id == email_id))
    ).scalars().all()
    assert attachments == []

    suggestions = (
        await db_session.execute(
            select(ProductSuggestion).where(ProductSuggestion.email_message_id == email_id)
        )
    ).scalars().all()
    assert suggestions == []


async def test_delete_mailbox_removes_contacts_and_cases_left_with_no_mail(
    api_client, db_session: AsyncSession, full_mailbox: dict
) -> None:
    contact = full_mailbox["contact"]
    case = full_mailbox["case"]
    mailbox = full_mailbox["mailbox"]

    await api_client.delete(f"/api/mailboxes/{mailbox.id}")

    assert (await db_session.get(Contact, contact.id)) is None
    assert (await db_session.get(Case, case.id)) is None
    case_contacts = (
        await db_session.execute(select(CaseContact).where(CaseContact.case_id == case.id))
    ).scalars().all()
    assert case_contacts == []


async def test_delete_mailbox_preserves_a_contact_still_used_by_another_mailbox(
    api_client, db_session: AsyncSession, tenant: Tenant, full_mailbox: dict
) -> None:
    """The contact/case cleanup must only remove rows left with *zero*
    remaining mail anywhere in the tenant - not every contact/case the
    deleted mailbox's mail ever touched. A contact who also emailed a
    second connected mailbox of the same tenant must survive."""
    contact = full_mailbox["contact"]
    case = full_mailbox["case"]
    mailbox_to_delete = full_mailbox["mailbox"]

    other_user = await _make_user(db_session, tenant)
    other_mailbox = await _make_mailbox(db_session, tenant, other_user)
    other_email = await _make_email(
        db_session, tenant=tenant, mailbox=other_mailbox, contact=contact, case=case
    )
    await db_session.commit()

    response = await api_client.delete(f"/api/mailboxes/{mailbox_to_delete.id}")
    assert response.status_code == 204

    assert (await db_session.get(Contact, contact.id)) is not None
    assert (await db_session.get(Case, case.id)) is not None
    # The other mailbox's own mail must be untouched.
    assert (await db_session.get(EmailMessage, other_email.id)) is not None


async def test_deleting_an_unknown_mailbox_is_a_404(api_client) -> None:
    response = await api_client.delete(f"/api/mailboxes/{uuid.uuid4()}")
    assert response.status_code == 404


async def test_a_mailbox_with_no_mail_at_all_deletes_cleanly(
    api_client, db_session: AsyncSession, tenant: Tenant
) -> None:
    """The common case for a freshly connected, never-polled mailbox -
    nothing to cascade, nothing to clean up."""
    user = await _make_user(db_session, tenant)
    mailbox = await _make_mailbox(db_session, tenant, user)
    await db_session.commit()

    response = await api_client.delete(f"/api/mailboxes/{mailbox.id}")
    assert response.status_code == 204
    assert (await db_session.get(Mailbox, mailbox.id)) is None
