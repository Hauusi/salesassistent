"""Tests for the contact overview/pipeline endpoint.

Covers the derived fields (total_inquiries, last_status, needs_followup)
since those are computed in the route, not stored - a wrong join or a
flipped comparison is invisible until this actually queries a contact with
a real history.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.case import Case, CaseContact
from app.models.contact import Contact
from app.models.email_message import EmailMessage
from app.models.enums import CaseStatus, EmailStatus, TypKategorie, WichtigkeitsKategorie
from app.models.mailbox import Mailbox
from app.models.tenant import Tenant
from app.models.user import User


@pytest.fixture
async def mailbox(db_session: AsyncSession, tenant: Tenant) -> Mailbox:
    user = (await db_session.execute(__import__("sqlalchemy").select(User).limit(1))).scalar_one()
    mailbox = Mailbox(tenant_id=tenant.id, user_id=user.id, email_address="me@example.com")
    db_session.add(mailbox)
    await db_session.flush()
    return mailbox


def _email(
    *, tenant_id, mailbox_id, contact_id, case_id=None, status: EmailStatus, days_ago: int, subject: str
) -> EmailMessage:
    return EmailMessage(
        tenant_id=tenant_id,
        mailbox_id=mailbox_id,
        contact_id=contact_id,
        case_id=case_id,
        gmail_message_id=f"gm-{uuid.uuid4().hex[:8]}",
        subject=subject,
        sender_address="kunde@example.com",
        raw_content="Inhalt",
        snippet="Inhalt",
        wichtigkeits_kategorie=WichtigkeitsKategorie.ANTWORT_ERFORDERLICH,
        typ=TypKategorie.ANFRAGE,
        status=status,
        received_at=datetime.now(UTC) - timedelta(days=days_ago),
    )


async def test_contact_with_only_a_stale_open_inquiry_needs_followup(
    api_client, db_session: AsyncSession, tenant: Tenant, mailbox: Mailbox
) -> None:
    contact = Contact(tenant_id=tenant.id, email_address="stale@customer.de", name="Stale Kunde")
    db_session.add(contact)
    await db_session.flush()
    db_session.add(
        _email(
            tenant_id=tenant.id,
            mailbox_id=mailbox.id,
            contact_id=contact.id,
            status=EmailStatus.WARTET_AUF_FREIGABE,
            days_ago=10,
            subject="Alte Anfrage",
        )
    )
    await db_session.commit()

    response = await api_client.get("/api/contacts")
    assert response.status_code == 200, response.text
    [row] = [r for r in response.json() if r["id"] == str(contact.id)]
    assert row["total_inquiries"] == 1
    assert row["last_status"] == "offen"
    assert row["needs_followup"] is True


async def test_contact_with_a_recent_open_inquiry_does_not_need_followup(
    api_client, db_session: AsyncSession, tenant: Tenant, mailbox: Mailbox
) -> None:
    contact = Contact(tenant_id=tenant.id, email_address="frisch@customer.de", name="Frischer Kunde")
    db_session.add(contact)
    await db_session.flush()
    db_session.add(
        _email(
            tenant_id=tenant.id,
            mailbox_id=mailbox.id,
            contact_id=contact.id,
            status=EmailStatus.WARTET_AUF_FREIGABE,
            days_ago=1,
            subject="Neue Anfrage",
        )
    )
    await db_session.commit()

    response = await api_client.get("/api/contacts")
    assert response.status_code == 200, response.text
    [row] = [r for r in response.json() if r["id"] == str(contact.id)]
    assert row["last_status"] == "offen"
    assert row["needs_followup"] is False


async def test_answered_status_comes_from_the_latest_email(
    api_client, db_session: AsyncSession, tenant: Tenant, mailbox: Mailbox
) -> None:
    contact = Contact(tenant_id=tenant.id, email_address="beantwortet@customer.de", name="Beantwortet")
    db_session.add(contact)
    await db_session.flush()
    db_session.add_all(
        [
            _email(
                tenant_id=tenant.id,
                mailbox_id=mailbox.id,
                contact_id=contact.id,
                status=EmailStatus.WARTET_AUF_FREIGABE,
                days_ago=20,
                subject="Erste Anfrage",
            ),
            _email(
                tenant_id=tenant.id,
                mailbox_id=mailbox.id,
                contact_id=contact.id,
                status=EmailStatus.ERLEDIGT,
                days_ago=1,
                subject="Zweite Anfrage",
            ),
        ]
    )
    await db_session.commit()

    response = await api_client.get("/api/contacts")
    assert response.status_code == 200, response.text
    [row] = [r for r in response.json() if r["id"] == str(contact.id)]
    assert row["total_inquiries"] == 2
    assert row["last_status"] == "beantwortet"
    assert row["needs_followup"] is False


async def test_closed_case_reports_abgeschlossen_even_if_stale(
    api_client, db_session: AsyncSession, tenant: Tenant, mailbox: Mailbox
) -> None:
    contact = Contact(tenant_id=tenant.id, email_address="closed@customer.de", name="Closed")
    db_session.add(contact)
    await db_session.flush()

    case = Case(tenant_id=tenant.id, title="Erledigter Fall", status=CaseStatus.GESCHLOSSEN)
    db_session.add(case)
    await db_session.flush()
    db_session.add(CaseContact(case_id=case.id, contact_id=contact.id))
    db_session.add(
        _email(
            tenant_id=tenant.id,
            mailbox_id=mailbox.id,
            contact_id=contact.id,
            case_id=case.id,
            status=EmailStatus.WARTET_AUF_FREIGABE,
            days_ago=30,
            subject="Alter Fall",
        )
    )
    await db_session.commit()

    response = await api_client.get("/api/contacts")
    assert response.status_code == 200, response.text
    [row] = [r for r in response.json() if r["id"] == str(contact.id)]
    assert row["last_status"] == "abgeschlossen"
    # A closed case never needs a follow-up, however old it is.
    assert row["needs_followup"] is False


async def test_followup_days_query_param_overrides_the_default(
    api_client, db_session: AsyncSession, tenant: Tenant, mailbox: Mailbox
) -> None:
    contact = Contact(tenant_id=tenant.id, email_address="grenzfall@customer.de", name="Grenzfall")
    db_session.add(contact)
    await db_session.flush()
    db_session.add(
        _email(
            tenant_id=tenant.id,
            mailbox_id=mailbox.id,
            contact_id=contact.id,
            status=EmailStatus.WARTET_AUF_FREIGABE,
            days_ago=3,
            subject="Anfrage",
        )
    )
    await db_session.commit()

    default_response = await api_client.get("/api/contacts")
    [row] = [r for r in default_response.json() if r["id"] == str(contact.id)]
    assert row["needs_followup"] is False  # default threshold is 5 days

    override_response = await api_client.get("/api/contacts?followup_days=2")
    [row] = [r for r in override_response.json() if r["id"] == str(contact.id)]
    assert row["needs_followup"] is True


async def test_contact_detail_returns_full_history_chronologically(
    api_client, db_session: AsyncSession, tenant: Tenant, mailbox: Mailbox
) -> None:
    contact = Contact(tenant_id=tenant.id, email_address="historie@customer.de", name="Historie")
    db_session.add(contact)
    await db_session.flush()
    db_session.add_all(
        [
            _email(
                tenant_id=tenant.id,
                mailbox_id=mailbox.id,
                contact_id=contact.id,
                status=EmailStatus.ERLEDIGT,
                days_ago=5,
                subject="Erste Mail",
            ),
            _email(
                tenant_id=tenant.id,
                mailbox_id=mailbox.id,
                contact_id=contact.id,
                status=EmailStatus.WARTET_AUF_FREIGABE,
                days_ago=1,
                subject="Zweite Mail",
            ),
        ]
    )
    await db_session.commit()

    response = await api_client.get(f"/api/contacts/{contact.id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_inquiries"] == 2
    assert [e["subject"] for e in body["emails"]] == ["Zweite Mail", "Erste Mail"]


async def test_unknown_contact_is_a_404(api_client, tenant: Tenant) -> None:
    response = await api_client.get(f"/api/contacts/{uuid.uuid4()}")
    assert response.status_code == 404
