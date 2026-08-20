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


async def test_attachment_metadata_is_persisted(
    db_session: AsyncSession, mailbox: Mailbox, monkeypatch
) -> None:
    """The parser collected this and the table existed, but nothing ever
    wrote a row - while the README claimed the metadata was captured."""
    from app.models.attachment import Attachment
    from app.services.gmail_client import FetchedAttachment

    _patch_llm_and_embeddings(monkeypatch, ANFRAGE_MAIL)
    fetched = _fetched_from_fixture(ANFRAGE_MAIL)
    fetched.attachments = [
        FetchedAttachment(
            filename="angebot.pdf",
            content_type="application/pdf",
            size_bytes=2048,
            gmail_attachment_id="att-1",
        ),
        FetchedAttachment(
            filename="zeichnung.dwg", content_type=None, size_bytes=None, gmail_attachment_id=None
        ),
    ]

    email = await pipeline.process_incoming_email(db_session, mailbox=mailbox, fetched=fetched)

    stored = (
        await db_session.execute(
            select(Attachment).where(Attachment.email_message_id == email.id)
        )
    ).scalars().all()

    assert {a.filename for a in stored} == {"angebot.pdf", "zeichnung.dwg"}
    pdf = next(a for a in stored if a.filename == "angebot.pdf")
    assert pdf.content_type == "application/pdf"
    assert pdf.size_bytes == 2048
    assert pdf.gmail_attachment_id == "att-1"
    assert pdf.tenant_id == email.tenant_id


async def test_a_mail_without_attachments_creates_no_rows(
    db_session: AsyncSession, mailbox: Mailbox, monkeypatch
) -> None:
    from app.models.attachment import Attachment

    _patch_llm_and_embeddings(monkeypatch, INFORMATION_MAIL)
    email = await pipeline.process_incoming_email(
        db_session, mailbox=mailbox, fetched=_fetched_from_fixture(INFORMATION_MAIL)
    )

    stored = (
        await db_session.execute(
            select(Attachment).where(Attachment.email_message_id == email.id)
        )
    ).scalars().all()
    assert stored == []


async def test_an_empty_generated_body_does_not_create_a_blank_draft(
    db_session: AsyncSession, mailbox: Mailbox, monkeypatch
) -> None:
    """A blank draft puts an empty editor in front of a human with no
    indication of why - better to record the mail and generate nothing."""
    _patch_llm_and_embeddings(monkeypatch, ANFRAGE_MAIL)

    async def _empty_draft(_db, *, email, client=None):
        return "Re: Betreff", "   ", "Kein RAG-Kontext verfügbar."

    monkeypatch.setattr(pipeline, "generate_draft", _empty_draft)

    email = await pipeline.process_incoming_email(
        db_session, mailbox=mailbox, fetched=_fetched_from_fixture(ANFRAGE_MAIL)
    )

    assert email is not None
    assert email.status is EmailStatus.WARTET_AUF_FREIGABE
    drafts = (
        await db_session.execute(select(Draft).where(Draft.email_message_id == email.id))
    ).scalars().all()
    assert drafts == []


async def test_every_category_records_a_filing_action(
    db_session: AsyncSession, mailbox: Mailbox, monkeypatch
) -> None:
    """The per-category behaviour is table-driven; this pins that every
    non-reply category still lands in the audit trail."""
    expected = {
        "information": "filed_as_information",
        "newsletter": "labeled_newsletter",
        "spam_verdacht": "hidden_as_spam_verdacht",
    }
    for fixture in (INFORMATION_MAIL, NEWSLETTER_MAIL, SPAM_MAIL):
        _patch_llm_and_embeddings(monkeypatch, fixture)
        email = await pipeline.process_incoming_email(
            db_session, mailbox=mailbox, fetched=_fetched_from_fixture(fixture)
        )
        actions = (
            await db_session.execute(
                select(ActionLog.action).where(ActionLog.entity_id == email.id)
            )
        ).scalars().all()
        wanted = expected[fixture["expected"]["wichtigkeits_kategorie"]]
        assert wanted in actions, f"{wanted} fehlt für {fixture['expected']}"
