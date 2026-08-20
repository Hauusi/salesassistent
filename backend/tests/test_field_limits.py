"""Bounded columns must survive the values real mail actually carries.

A multi-kilobyte display name is a routine spam pattern and a folded
Subject header exceeds 998 characters easily. Writing either used to raise
StringDataRightTruncation from Postgres and abort the whole poll batch.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.case import Case
from app.models.contact import Contact
from app.models.email_message import EmailMessage
from app.models.enums import TypKategorie, WichtigkeitsKategorie
from app.models.limits import column_max_length, fit
from app.models.mailbox import Mailbox
from app.models.tenant import Tenant
from app.models.user import User
from app.services import pipeline
from app.services.classification import ClassificationResult
from app.services.gmail_client import FetchedEmail


def test_fit_leaves_a_short_value_untouched() -> None:
    assert fit("Angebotsanfrage", EmailMessage, "subject") == "Angebotsanfrage"


def test_fit_passes_none_through_for_nullable_columns() -> None:
    assert fit(None, EmailMessage, "subject") is None


def test_fit_truncates_to_the_declared_column_length() -> None:
    fitted = fit("x" * 5000, EmailMessage, "sender_name")
    assert len(fitted) == column_max_length(EmailMessage, "sender_name")


def test_fit_marks_a_truncated_value_as_truncated() -> None:
    """A silently cut value is indistinguishable from the sender's own
    text to whoever reads it later."""
    assert fit("x" * 5000, EmailMessage, "sender_name").endswith("…")


def test_fit_is_a_no_op_for_unbounded_text_columns() -> None:
    body = "x" * 50_000
    assert fit(body, EmailMessage, "raw_content") == body


@pytest.fixture
async def mailbox(db_session: AsyncSession) -> Mailbox:
    tenant = Tenant(name="Test", slug=f"test-{uuid.uuid4().hex[:8]}")
    db_session.add(tenant)
    await db_session.flush()
    user = User(tenant_id=tenant.id, email="user@example.com")
    db_session.add(user)
    await db_session.flush()
    mailbox = Mailbox(tenant_id=tenant.id, user_id=user.id, email_address="me@example.com")
    db_session.add(mailbox)
    await db_session.flush()
    return mailbox


async def test_oversized_headers_are_persisted_instead_of_aborting_the_batch(
    db_session: AsyncSession, mailbox: Mailbox, monkeypatch
) -> None:
    monkeypatch.setattr(
        pipeline.classification,
        "classify_email",
        lambda **_kw: _classification(),
    )
    monkeypatch.setattr(pipeline.embeddings, "embed_text", lambda *_a, **_kw: _embedding())

    fetched = FetchedEmail(
        gmail_message_id="gm-oversized",
        gmail_thread_id="thread-1",
        rfc822_message_id="<" + "m" * 4000 + "@spam.example>",
        subject="Ü" * 4000,
        sender_address="spam@example.com",
        sender_name="N" * 4000,
        raw_content="Kurzer Text.",
        snippet="S" * 4000,
        received_at=datetime.now(UTC),
    )

    email = await pipeline.process_incoming_email(db_session, mailbox=mailbox, fetched=fetched)

    assert email is not None
    stored = (
        await db_session.execute(select(EmailMessage).where(EmailMessage.id == email.id))
    ).scalar_one()
    assert len(stored.sender_name) <= column_max_length(EmailMessage, "sender_name")
    assert len(stored.subject) <= column_max_length(EmailMessage, "subject")
    assert len(stored.snippet) <= column_max_length(EmailMessage, "snippet")
    assert len(stored.rfc822_message_id) <= column_max_length(EmailMessage, "rfc822_message_id")


async def test_an_oversized_suggested_case_title_is_fitted(
    db_session: AsyncSession, mailbox: Mailbox, monkeypatch
) -> None:
    monkeypatch.setattr(
        pipeline.classification,
        "classify_email",
        lambda **_kw: _classification(title="T" * 3000),
    )
    monkeypatch.setattr(pipeline.embeddings, "embed_text", lambda *_a, **_kw: _embedding())

    fetched = FetchedEmail(
        gmail_message_id="gm-title",
        gmail_thread_id=None,
        rfc822_message_id=None,
        subject="Betreff",
        sender_address="kunde@example.com",
        sender_name="Kunde",
        raw_content="Text",
        snippet=None,
        received_at=datetime.now(UTC),
    )
    email = await pipeline.process_incoming_email(db_session, mailbox=mailbox, fetched=fetched)

    case = (await db_session.execute(select(Case).where(Case.id == email.case_id))).scalar_one()
    assert len(case.title) <= column_max_length(Case, "title")


async def test_an_oversized_sender_name_is_fitted_on_the_contact_too(
    db_session: AsyncSession, mailbox: Mailbox, monkeypatch
) -> None:
    monkeypatch.setattr(pipeline.classification, "classify_email", lambda **_kw: _classification())
    monkeypatch.setattr(pipeline.embeddings, "embed_text", lambda *_a, **_kw: _embedding())

    fetched = FetchedEmail(
        gmail_message_id="gm-contact",
        gmail_thread_id=None,
        rfc822_message_id=None,
        subject="Betreff",
        sender_address="lang@example.com",
        sender_name="N" * 4000,
        raw_content="Text",
        snippet=None,
        received_at=datetime.now(UTC),
    )
    await pipeline.process_incoming_email(db_session, mailbox=mailbox, fetched=fetched)

    contact = (
        await db_session.execute(select(Contact).where(Contact.email_address == "lang@example.com"))
    ).scalar_one()
    assert len(contact.name) <= column_max_length(Contact, "name")


async def _classification(title: str | None = None) -> ClassificationResult:
    return ClassificationResult(
        wichtigkeits_kategorie=WichtigkeitsKategorie.INFORMATION,
        typ=TypKategorie.KEINER,
        confidence=0.9,
        reasoning="Test",
        suggested_case_title=title,
    )


async def _embedding() -> list[float]:
    return [0.1] * 1024
