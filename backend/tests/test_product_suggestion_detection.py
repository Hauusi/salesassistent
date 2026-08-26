"""Tests for automatic product-suggestion detection: an incoming mail with
its own article number + description should produce a pending
ProductSuggestion, without ever costing a second Claude call - detection
piggybacks on the existing classify_email tool call (see
app/services/classification.py and app/services/product_suggestion_service.py).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.action_log import ActionLog
from app.models.enums import (
    ProductSuggestionStatus,
    TypKategorie,
    WichtigkeitsKategorie,
)
from app.models.mailbox import Mailbox
from app.models.product import Product
from app.models.product_suggestion import ProductSuggestion
from app.models.tenant import Tenant
from app.models.user import User
from app.services import pipeline
from app.services.classification import ClassificationResult, classify_email
from app.services.gmail_client import FetchedEmail
from tests.mocks import FakeAnthropicClient, tool_response

_SUPPLIER_MAIL_BODY = (
    "Guten Tag,\n\n"
    "wir freuen uns, Ihnen unser neues Produkt vorzustellen:\n\n"
    "Artikelnummer: ALU-9090\n"
    "Aluminiumprofil 90x90, eloxiert, Nutbreite 10mm, ideal für Maschinenbau-Rahmen.\n\n"
    "Mit freundlichen Grüßen"
)


def _fetched(**overrides) -> FetchedEmail:
    defaults = {
        "gmail_message_id": f"gm-{uuid.uuid4().hex}",
        "gmail_thread_id": "thread-1",
        "rfc822_message_id": "<abc@lieferant.de>",
        "subject": "Neues Produkt: Aluminiumprofil 90x90",
        "sender_address": "lieferant@example.com",
        "sender_name": "Lieferant GmbH",
        "raw_content": _SUPPLIER_MAIL_BODY,
        "snippet": _SUPPLIER_MAIL_BODY[:100],
        "received_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return FetchedEmail(**defaults)


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


def _detection_result(**overrides) -> ClassificationResult:
    defaults = {
        "wichtigkeits_kategorie": WichtigkeitsKategorie.INFORMATION,
        "typ": TypKategorie.KEINER,
        "confidence": 0.9,
        "reasoning": "Produktankündigung",
        "detected_product_sku": "ALU-9090",
        "detected_product_name": "Aluminiumprofil 90x90",
        "detected_product_description": "Eloxiert, Nutbreite 10mm, für Maschinenbau-Rahmen.",
    }
    defaults.update(overrides)
    return ClassificationResult(**defaults)


def _patch_llm_and_embeddings(monkeypatch, result: ClassificationResult) -> None:
    monkeypatch.setattr(pipeline.classification, "classify_email", AsyncMock(return_value=result))
    monkeypatch.setattr(pipeline.embeddings, "embed_text", AsyncMock(return_value=[0.1] * 1024))


# --- classify_email: the tool call itself extracts the product fields -----


async def test_classify_email_extracts_a_detected_article_from_the_tool_response() -> None:
    """The same forced-tool-use call that classifies the mail also carries
    the detected article - no second Claude call involved."""

    def _respond(_kwargs: dict):
        return tool_response(
            wichtigkeits_kategorie="information",
            typ="keiner",
            confidence=0.93,
            reasoning="Produktankündigung eines Lieferanten.",
            detected_product_sku="ALU-9090",
            detected_product_name="Aluminiumprofil 90x90",
            detected_product_description="Eloxiert, Nutbreite 10mm.",
        )

    client = FakeAnthropicClient(_respond)
    result = await classify_email(
        subject="Neues Produkt", sender_address="lieferant@example.com",
        body=_SUPPLIER_MAIL_BODY, client=client,
    )

    assert result.detected_product_sku == "ALU-9090"
    assert result.detected_product_name == "Aluminiumprofil 90x90"
    assert result.detected_product_description == "Eloxiert, Nutbreite 10mm."
    # Exactly one API call for both the classification and the detection.
    assert len(client.messages.calls) == 1


async def test_classify_email_leaves_product_fields_empty_when_nothing_detected() -> None:
    def _respond(_kwargs: dict):
        return tool_response(
            wichtigkeits_kategorie="information", typ="keiner",
            confidence=0.8, reasoning="Normale Mail.",
        )

    client = FakeAnthropicClient(_respond)
    result = await classify_email(
        subject="Frage", sender_address="kunde@example.com", body="Nur eine Frage.", client=client,
    )

    assert result.detected_product_sku is None
    assert result.detected_product_name is None
    assert result.detected_product_description is None


# --- pipeline: turning a detection into a pending ProductSuggestion -------


async def test_a_mail_with_a_clear_new_article_creates_a_pending_suggestion(
    db_session: AsyncSession, mailbox: Mailbox, monkeypatch
) -> None:
    _patch_llm_and_embeddings(monkeypatch, _detection_result())

    email = await pipeline.process_incoming_email(db_session, mailbox=mailbox, fetched=_fetched())
    assert email is not None

    suggestions = (
        await db_session.execute(
            select(ProductSuggestion).where(ProductSuggestion.email_message_id == email.id)
        )
    ).scalars().all()
    assert len(suggestions) == 1
    suggestion = suggestions[0]
    assert suggestion.sku == "ALU-9090"
    assert suggestion.name == "Aluminiumprofil 90x90"
    assert "Nutbreite 10mm" in suggestion.description
    assert suggestion.status == ProductSuggestionStatus.VORGESCHLAGEN
    assert suggestion.tenant_id == email.tenant_id

    actions = (
        await db_session.execute(
            select(ActionLog.action).where(ActionLog.entity_id == suggestion.id)
        )
    ).scalars().all()
    assert "product_suggestion_created" in actions


async def test_no_suggestion_when_nothing_was_detected(
    db_session: AsyncSession, mailbox: Mailbox, monkeypatch
) -> None:
    _patch_llm_and_embeddings(
        monkeypatch,
        ClassificationResult(
            wichtigkeits_kategorie=WichtigkeitsKategorie.INFORMATION,
            typ=TypKategorie.KEINER,
            confidence=0.8,
            reasoning="Keine Produktankündigung.",
        ),
    )

    email = await pipeline.process_incoming_email(
        db_session, mailbox=mailbox, fetched=_fetched(gmail_message_id=f"gm-{uuid.uuid4().hex}")
    )
    assert email is not None

    suggestions = (
        await db_session.execute(select(ProductSuggestion))
    ).scalars().all()
    assert suggestions == []


async def test_no_suggestion_when_the_sku_already_exists_in_the_catalog(
    db_session: AsyncSession, mailbox: Mailbox, monkeypatch
) -> None:
    db_session.add(
        Product(tenant_id=mailbox.tenant_id, name="Aluminiumprofil 90x90 (alt)", sku="ALU-9090")
    )
    await db_session.commit()

    _patch_llm_and_embeddings(monkeypatch, _detection_result())
    email = await pipeline.process_incoming_email(db_session, mailbox=mailbox, fetched=_fetched())
    assert email is not None

    suggestions = (
        await db_session.execute(select(ProductSuggestion))
    ).scalars().all()
    assert suggestions == [], "an SKU already in the catalog must not produce a suggestion"


async def test_a_second_mail_about_the_same_new_sku_does_not_duplicate_the_suggestion(
    db_session: AsyncSession, mailbox: Mailbox, monkeypatch
) -> None:
    """Two different mails mentioning the same not-yet-catalogued SKU must
    not pile up two pending suggestions for a human to review separately."""
    _patch_llm_and_embeddings(monkeypatch, _detection_result())
    await pipeline.process_incoming_email(
        db_session, mailbox=mailbox, fetched=_fetched(gmail_message_id="gm-first")
    )

    _patch_llm_and_embeddings(monkeypatch, _detection_result(reasoning="Zweite Erwähnung"))
    await pipeline.process_incoming_email(
        db_session, mailbox=mailbox, fetched=_fetched(gmail_message_id="gm-second")
    )

    suggestions = (
        await db_session.execute(
            select(ProductSuggestion).where(ProductSuggestion.sku == "ALU-9090")
        )
    ).scalars().all()
    assert len(suggestions) == 1


async def test_a_detected_sku_without_a_description_is_not_a_suggestion(
    db_session: AsyncSession, mailbox: Mailbox, monkeypatch
) -> None:
    """The tool schema only asks for a detection when both the SKU and its
    description are present - guards the same contract on the pipeline
    side in case a response is inconsistent."""
    _patch_llm_and_embeddings(
        monkeypatch,
        _detection_result(detected_product_description=None),
    )

    await pipeline.process_incoming_email(db_session, mailbox=mailbox, fetched=_fetched())

    suggestions = (await db_session.execute(select(ProductSuggestion))).scalars().all()
    assert suggestions == []
