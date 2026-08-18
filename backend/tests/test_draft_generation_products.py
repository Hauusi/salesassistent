"""Tests that app.services.draft_generation.generate_draft pulls product
context into the prompt for Angebotsanfragen (typ=anfrage) and leaves it
out otherwise - the actual LLM call is mocked (tests/mocks.py).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email_message import EmailMessage
from app.models.enums import TypKategorie, WichtigkeitsKategorie
from app.models.mailbox import Mailbox
from app.models.product import Product
from app.models.tenant import Tenant
from app.models.user import User
from app.services.draft_generation import generate_draft
from tests.mocks import FakeAnthropicClient, tool_response


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


async def _make_email(db_session: AsyncSession, mailbox: Mailbox, *, typ: TypKategorie, content: str) -> EmailMessage:
    email = EmailMessage(
        tenant_id=mailbox.tenant_id,
        mailbox_id=mailbox.id,
        gmail_message_id=f"gm-{uuid.uuid4().hex}",
        sender_address="kunde@example.com",
        subject="Anfrage Aluminiumprofile",
        raw_content=content,
        typ=typ,
        wichtigkeits_kategorie=WichtigkeitsKategorie.ANTWORT_ERFORDERLICH,
        received_at=datetime.now(timezone.utc),
    )
    db_session.add(email)
    await db_session.flush()
    return email


def _fake_client() -> FakeAnthropicClient:
    def _respond(_kwargs: dict):
        return tool_response(subject="Re: Anfrage", body="Vielen Dank fuer Ihre Anfrage.")

    return FakeAnthropicClient(_respond)


async def test_generate_draft_includes_matching_products_for_anfrage(
    db_session: AsyncSession, mailbox: Mailbox
) -> None:
    db_session.add(
        Product(
            tenant_id=mailbox.tenant_id,
            name="Aluminiumprofil AP-40",
            category="Profile",
            description="Eloxiertes Aluminiumprofil",
            price=12.5,
            currency="EUR",
            availability="3-5 Werktage",
            specs={"Laenge": "3m"},
        )
    )
    await db_session.flush()

    email = await _make_email(
        db_session,
        mailbox,
        typ=TypKategorie.ANFRAGE,
        content="Bitte senden Sie uns ein Angebot fuer 200 Stueck Aluminiumprofile, Laenge 3m.",
    )

    client = _fake_client()
    subject, body, rag_summary = await generate_draft(db_session, email=email, client=client)

    assert subject == "Re: Anfrage"
    sent_prompt = client.messages.calls[0]["messages"][0]["content"]
    assert "Aluminiumprofil AP-40" in sent_prompt
    assert "12.50 EUR" in sent_prompt
    assert "3-5 Werktage" in sent_prompt
    assert "Aluminiumprofil AP-40" in rag_summary


async def test_generate_draft_anfrage_without_product_match_has_no_product_block(
    db_session: AsyncSession, mailbox: Mailbox
) -> None:
    email = await _make_email(
        db_session,
        mailbox,
        typ=TypKategorie.ANFRAGE,
        content="Bitte senden Sie uns ein Angebot fuer Ihre Dienstleistungen.",
    )

    client = _fake_client()
    _subject, _body, rag_summary = await generate_draft(db_session, email=email, client=client)

    sent_prompt = client.messages.calls[0]["messages"][0]["content"]
    assert "Produkt-Wissensbasis" not in sent_prompt
    assert "Keine passenden Produkte" in rag_summary


async def test_generate_draft_non_anfrage_skips_product_search_entirely(
    db_session: AsyncSession, mailbox: Mailbox
) -> None:
    db_session.add(
        Product(tenant_id=mailbox.tenant_id, name="Aluminiumprofil AP-40", category="Profile")
    )
    await db_session.flush()

    email = await _make_email(
        db_session,
        mailbox,
        typ=TypKategorie.KEINER,
        content="Koennen Sie mir kurz den Status meiner letzten Bestellung mitteilen?",
    )

    client = _fake_client()
    _subject, _body, rag_summary = await generate_draft(db_session, email=email, client=client)

    sent_prompt = client.messages.calls[0]["messages"][0]["content"]
    assert "Produkt-Wissensbasis" not in sent_prompt
    assert "Produkte" not in rag_summary


async def test_generate_draft_marks_system_prompt_cacheable(
    db_session: AsyncSession, mailbox: Mailbox
) -> None:
    email = await _make_email(
        db_session, mailbox, typ=TypKategorie.KEINER, content="Kurze Frage zum Liefertermin.",
    )

    client = _fake_client()
    await generate_draft(db_session, email=email, client=client)

    call = client.messages.calls[0]
    assert call["system"][-1]["cache_control"] == {"type": "ephemeral"}


async def test_generate_draft_strips_quoted_thread_from_new_mail(
    db_session: AsyncSession, mailbox: Mailbox
) -> None:
    email = await _make_email(
        db_session,
        mailbox,
        typ=TypKategorie.KEINER,
        content=(
            "Danke, das reicht mir.\n\n"
            "Am Mo., 10. Aug. 2026 um 09:00 schrieb Alt Absender <alt@example.com>:\n"
            "> Ein alter, fuer den Entwurf irrelevanter Thread-Verlauf."
        ),
    )

    client = _fake_client()
    await generate_draft(db_session, email=email, client=client)

    sent_prompt = client.messages.calls[0]["messages"][0]["content"]
    assert "Danke, das reicht mir." in sent_prompt
    assert "irrelevanter Thread-Verlauf" not in sent_prompt
