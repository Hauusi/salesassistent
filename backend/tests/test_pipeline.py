"""End-to-end pipeline tests: classification -> contact/case assignment ->
per-category action logic (concept 5.3), against a real DB with the LLM/
embedding calls mocked out (tests/mocks.py + unittest.mock).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.action_log import ActionLog
from app.models.draft import Draft
from app.models.email_message import EmailMessage
from app.models.enums import DraftStatus, EmailStatus, TypKategorie, WichtigkeitsKategorie
from app.models.mailbox import Mailbox
from app.models.tenant import Tenant
from app.models.user import User
from app.services import pipeline
from app.services.classification import ClassificationResult
from app.services.gmail_client import FetchedEmail
from tests.fixtures.emails import ANFRAGE_MAIL, BESTELLUNG_MAIL, INFORMATION_MAIL, NEWSLETTER_MAIL, SPAM_MAIL


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


def _fetched_from_fixture(fixture: dict) -> FetchedEmail:
    return FetchedEmail(
        gmail_message_id=f"gm-{uuid.uuid4().hex}",
        gmail_thread_id="thread-1",
        rfc822_message_id="<abc@customer.de>",
        subject=fixture["subject"],
        sender_address=fixture["sender_address"],
        sender_name=fixture["sender_name"],
        raw_content=fixture["body"],
        snippet=fixture["body"][:100],
        received_at=datetime.now(timezone.utc),
    )


def _patch_llm_and_embeddings(monkeypatch, fixture: dict) -> None:
    result = ClassificationResult(
        wichtigkeits_kategorie=WichtigkeitsKategorie(fixture["expected"]["wichtigkeits_kategorie"]),
        typ=TypKategorie(fixture["expected"]["typ"]),
        confidence=0.9,
        reasoning="Testbegründung",
        suggested_case_title=fixture["subject"],
    )
    monkeypatch.setattr(pipeline.classification, "classify_email", AsyncMock(return_value=result))
    monkeypatch.setattr(pipeline.embeddings, "embed_text", AsyncMock(return_value=[0.1] * 1024))
    monkeypatch.setattr(
        pipeline, "generate_draft", AsyncMock(return_value=("Re: Test", "Ein Antworttext.", "1 Mail als Kontext."))
    )


async def _action_names(db_session: AsyncSession, entity_id) -> list[str]:
    result = await db_session.execute(select(ActionLog.action).where(ActionLog.entity_id == entity_id))
    return list(result.scalars().all())


async def test_anfrage_mail_creates_draft_and_waits_for_approval(
    db_session: AsyncSession, mailbox: Mailbox, monkeypatch
) -> None:
    _patch_llm_and_embeddings(monkeypatch, ANFRAGE_MAIL)

    email = await pipeline.process_incoming_email(
        db_session, mailbox=mailbox, fetched=_fetched_from_fixture(ANFRAGE_MAIL)
    )

    assert email is not None
    assert email.wichtigkeits_kategorie == WichtigkeitsKategorie.ANTWORT_ERFORDERLICH
    assert email.typ == TypKategorie.ANFRAGE
    assert email.status == EmailStatus.WARTET_AUF_FREIGABE

    draft_result = await db_session.execute(select(Draft).where(Draft.email_message_id == email.id))
    draft = draft_result.scalar_one()
    assert draft.status == DraftStatus.ENTWURF
    assert draft.body == "Ein Antworttext."

    actions = await _action_names(db_session, email.id)
    assert "classified" in actions
    assert "case_created" in actions


async def test_bestellung_mail_gets_typ_bestellung_not_anfrage(
    db_session: AsyncSession, mailbox: Mailbox, monkeypatch
) -> None:
    _patch_llm_and_embeddings(monkeypatch, BESTELLUNG_MAIL)

    email = await pipeline.process_incoming_email(
        db_session, mailbox=mailbox, fetched=_fetched_from_fixture(BESTELLUNG_MAIL)
    )

    assert email is not None
    assert email.typ == TypKategorie.BESTELLUNG
    assert email.typ != TypKategorie.ANFRAGE
    # bestellung is still antwort_erforderlich -> also gets a draft (orthogonal axes).
    assert email.status == EmailStatus.WARTET_AUF_FREIGABE
    draft_result = await db_session.execute(select(Draft).where(Draft.email_message_id == email.id))
    assert draft_result.scalar_one_or_none() is not None


async def test_information_mail_is_filed_without_draft(
    db_session: AsyncSession, mailbox: Mailbox, monkeypatch
) -> None:
    _patch_llm_and_embeddings(monkeypatch, INFORMATION_MAIL)

    email = await pipeline.process_incoming_email(
        db_session, mailbox=mailbox, fetched=_fetched_from_fixture(INFORMATION_MAIL)
    )

    assert email is not None
    assert email.status == EmailStatus.ABGELEGT
    draft_result = await db_session.execute(select(Draft).where(Draft.email_message_id == email.id))
    assert draft_result.scalar_one_or_none() is None
    assert "filed_as_information" in await _action_names(db_session, email.id)


async def test_newsletter_mail_only_labeled(db_session: AsyncSession, mailbox: Mailbox, monkeypatch) -> None:
    _patch_llm_and_embeddings(monkeypatch, NEWSLETTER_MAIL)

    email = await pipeline.process_incoming_email(
        db_session, mailbox=mailbox, fetched=_fetched_from_fixture(NEWSLETTER_MAIL)
    )

    assert email is not None
    assert email.status == EmailStatus.ERLEDIGT
    draft_result = await db_session.execute(select(Draft).where(Draft.email_message_id == email.id))
    assert draft_result.scalar_one_or_none() is None
    assert "labeled_newsletter" in await _action_names(db_session, email.id)


async def test_spam_verdacht_is_hidden_but_not_deleted(
    db_session: AsyncSession, mailbox: Mailbox, monkeypatch
) -> None:
    _patch_llm_and_embeddings(monkeypatch, SPAM_MAIL)

    email = await pipeline.process_incoming_email(
        db_session, mailbox=mailbox, fetched=_fetched_from_fixture(SPAM_MAIL)
    )

    assert email is not None
    assert email.status == EmailStatus.AUSGEBLENDET

    # Still present in the DB - "ausblenden, nicht löschen".
    reloaded = await db_session.get(EmailMessage, email.id)
    assert reloaded is not None
    assert "hidden_as_spam_verdacht" in await _action_names(db_session, email.id)


async def test_processing_same_gmail_message_twice_is_idempotent(
    db_session: AsyncSession, mailbox: Mailbox, monkeypatch
) -> None:
    _patch_llm_and_embeddings(monkeypatch, INFORMATION_MAIL)
    fetched = _fetched_from_fixture(INFORMATION_MAIL)

    first = await pipeline.process_incoming_email(db_session, mailbox=mailbox, fetched=fetched)
    second = await pipeline.process_incoming_email(db_session, mailbox=mailbox, fetched=fetched)

    assert first is not None
    assert second is None

    count_result = await db_session.execute(
        select(EmailMessage).where(EmailMessage.gmail_message_id == fetched.gmail_message_id)
    )
    assert len(count_result.scalars().all()) == 1
