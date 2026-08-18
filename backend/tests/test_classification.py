"""Tests for app.services.classification.

The Anthropic client is faked (see tests/mocks.py) so these tests check
our request construction and response parsing, not the model's actual
judgement - i.e. "does the plumbing map fixture -> ClassificationResult
correctly", using each category once per concept 5.3.
"""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.models.enums import TypKategorie, WichtigkeitsKategorie
from app.services.classification import classify_email
from tests.fixtures.emails import (
    ANFRAGE_MAIL,
    BESTELLUNG_MAIL,
    INFORMATION_MAIL,
    NEWSLETTER_MAIL,
    SPAM_MAIL,
)
from tests.mocks import FakeAnthropicClient, tool_response


def _client_for(expected: dict) -> FakeAnthropicClient:
    def _respond(_kwargs: dict):
        return tool_response(
            wichtigkeits_kategorie=expected["wichtigkeits_kategorie"],
            typ=expected["typ"],
            confidence=0.92,
            reasoning="Testbegründung.",
            suggested_case_title="Testfall",
        )

    return FakeAnthropicClient(_respond)


@pytest.mark.parametrize(
    "fixture",
    [ANFRAGE_MAIL, BESTELLUNG_MAIL, INFORMATION_MAIL, NEWSLETTER_MAIL, SPAM_MAIL],
    ids=lambda f: f["expected"]["wichtigkeits_kategorie"],
)
async def test_classify_email_maps_llm_output_to_result(fixture: dict) -> None:
    client = _client_for(fixture["expected"])

    result = await classify_email(
        subject=fixture["subject"],
        sender_address=fixture["sender_address"],
        body=fixture["body"],
        client=client,
    )

    assert result.wichtigkeits_kategorie == WichtigkeitsKategorie(fixture["expected"]["wichtigkeits_kategorie"])
    assert result.typ == TypKategorie(fixture["expected"]["typ"])
    assert result.confidence == pytest.approx(0.92)
    assert result.reasoning == "Testbegründung."


async def test_classify_email_distinguishes_bestellung_from_anfrage() -> None:
    """The concept explicitly calls out bestellung vs. anfrage as the key
    typ distinction to get right - both are antwort_erforderlich, but the
    orthogonal `typ` axis must differ."""
    anfrage_client = _client_for(ANFRAGE_MAIL["expected"])
    bestellung_client = _client_for(BESTELLUNG_MAIL["expected"])

    anfrage_result = await classify_email(
        subject=ANFRAGE_MAIL["subject"],
        sender_address=ANFRAGE_MAIL["sender_address"],
        body=ANFRAGE_MAIL["body"],
        client=anfrage_client,
    )
    bestellung_result = await classify_email(
        subject=BESTELLUNG_MAIL["subject"],
        sender_address=BESTELLUNG_MAIL["sender_address"],
        body=BESTELLUNG_MAIL["body"],
        client=bestellung_client,
    )

    assert anfrage_result.wichtigkeits_kategorie == bestellung_result.wichtigkeits_kategorie
    assert anfrage_result.typ == TypKategorie.ANFRAGE
    assert bestellung_result.typ == TypKategorie.BESTELLUNG
    assert anfrage_result.typ != bestellung_result.typ


async def test_classify_email_sends_forced_tool_choice_and_content() -> None:
    client = _client_for(ANFRAGE_MAIL["expected"])

    await classify_email(
        subject=ANFRAGE_MAIL["subject"],
        sender_address=ANFRAGE_MAIL["sender_address"],
        body=ANFRAGE_MAIL["body"],
        client=client,
    )

    assert len(client.messages.calls) == 1
    call = client.messages.calls[0]
    assert call["tool_choice"] == {"type": "tool", "name": "classify_email"}
    assert call["tools"][0]["name"] == "classify_email"
    user_content = call["messages"][0]["content"]
    assert ANFRAGE_MAIL["subject"] in user_content
    assert ANFRAGE_MAIL["sender_address"] in user_content


async def test_classify_email_uses_the_cheaper_classification_model() -> None:
    """Classification is a fixed-enum categorization task, not language
    generation - it should run on the cheaper model configured separately
    from draft generation's anthropic_model (cost optimization, see
    app/services/draft_generation.py for the Sonnet counterpart)."""
    client = _client_for(ANFRAGE_MAIL["expected"])

    await classify_email(
        subject=ANFRAGE_MAIL["subject"],
        sender_address=ANFRAGE_MAIL["sender_address"],
        body=ANFRAGE_MAIL["body"],
        client=client,
    )

    call = client.messages.calls[0]
    assert call["model"] == get_settings().anthropic_classification_model


async def test_classify_email_marks_system_prompt_cacheable() -> None:
    client = _client_for(ANFRAGE_MAIL["expected"])

    await classify_email(
        subject=ANFRAGE_MAIL["subject"],
        sender_address=ANFRAGE_MAIL["sender_address"],
        body=ANFRAGE_MAIL["body"],
        client=client,
    )

    call = client.messages.calls[0]
    assert call["system"][-1]["cache_control"] == {"type": "ephemeral"}


async def test_classify_email_strips_quoted_thread_from_prompt() -> None:
    client = _client_for(ANFRAGE_MAIL["expected"])
    body = (
        f"{ANFRAGE_MAIL['body']}\n\n"
        "Am Mo., 10. Aug. 2026 um 09:00 schrieb Alt Absender <alt@example.com>:\n"
        "> Ein alter, fuer die Klassifikation irrelevanter Thread-Verlauf."
    )

    await classify_email(
        subject=ANFRAGE_MAIL["subject"],
        sender_address=ANFRAGE_MAIL["sender_address"],
        body=body,
        client=client,
    )

    user_content = client.messages.calls[0]["messages"][0]["content"]
    assert "alter, fuer die Klassifikation irrelevanter" not in user_content


async def test_classify_email_raises_clear_error_without_tool_use() -> None:
    def _respond(_kwargs: dict):
        from tests.mocks import FakeMessage

        return FakeMessage(content=[])  # model answered in plain text, no tool call

    client = FakeAnthropicClient(_respond)

    with pytest.raises(ValueError, match="classify_email"):
        await classify_email(
            subject="Test", sender_address="a@b.de", body="Hallo", client=client
        )
