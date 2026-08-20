"""Tests for parsing a raw Gmail API message into a FetchedEmail.

This is the boundary where untrusted, externally-authored data enters the
system, and it had no test coverage. The cases below are the ones that
used to raise out of the poll loop and abort a whole batch (see
app/workers/tasks.py), plus the ones that quietly corrupted data.
"""
from __future__ import annotations

import base64
from datetime import timezone

from app.services.gmail_client import parse_gmail_message


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def _message(*, headers: list[dict], body: str = "Hallo", mime: str = "text/plain", **extra) -> dict:
    return {
        "id": extra.pop("id", "gm-1"),
        "threadId": "thread-1",
        "snippet": extra.pop("snippet", "Vorschau"),
        "payload": {"mimeType": mime, "headers": headers, "body": {"data": _b64(body)}},
        **extra,
    }


def test_parses_a_normal_message() -> None:
    raw = _message(
        headers=[
            {"name": "From", "value": "Julia Bauer <julia@musterkunde.de>"},
            {"name": "Subject", "value": "Angebotsanfrage"},
            {"name": "Date", "value": "Mon, 12 Aug 2026 10:15:00 +0200"},
            {"name": "Message-ID", "value": "<abc@musterkunde.de>"},
        ],
        body="Guten Tag, bitte um ein Angebot.",
    )

    fetched = parse_gmail_message(raw)

    assert fetched.sender_address == "julia@musterkunde.de"
    assert fetched.sender_name == "Julia Bauer"
    assert fetched.subject == "Angebotsanfrage"
    assert fetched.raw_content == "Guten Tag, bitte um ein Angebot."
    assert fetched.received_at.tzinfo is not None


def test_headers_are_matched_case_insensitively() -> None:
    raw = _message(headers=[{"name": "from", "value": "a@b.de"}, {"name": "SUBJECT", "value": "Hi"}])
    fetched = parse_gmail_message(raw)
    assert fetched.sender_address == "a@b.de"
    assert fetched.subject == "Hi"


def test_unparsable_sender_gets_a_per_message_placeholder() -> None:
    """A single shared placeholder collapsed every such mail onto one
    Contact, which glued unrelated mail into one Case."""
    first = parse_gmail_message(_message(headers=[{"name": "From", "value": ""}], id="gm-a"))
    second = parse_gmail_message(_message(headers=[{"name": "From", "value": ""}], id="gm-b"))

    assert first.sender_address != second.sender_address
    assert first.sender_address.endswith("@unknown.invalid")


def test_missing_date_header_falls_back_to_internal_date() -> None:
    raw = _message(headers=[{"name": "From", "value": "a@b.de"}], internalDate="1755000000000")
    fetched = parse_gmail_message(raw)
    assert fetched.received_at.tzinfo == timezone.utc


def test_unparsable_date_header_does_not_raise() -> None:
    raw = _message(
        headers=[{"name": "From", "value": "a@b.de"}, {"name": "Date", "value": "gestern irgendwann"}],
        internalDate="1755000000000",
    )
    assert parse_gmail_message(raw).received_at is not None


def test_naive_date_header_is_made_timezone_aware() -> None:
    raw = _message(headers=[{"name": "Date", "value": "Mon, 12 Aug 2026 10:15:00"}])
    assert parse_gmail_message(raw).received_at.tzinfo is not None


def test_undecodable_body_does_not_raise() -> None:
    """A malformed body part must cost this mail its content, not the batch."""
    raw = {
        "id": "gm-1",
        "payload": {
            "mimeType": "text/plain",
            "headers": [{"name": "From", "value": "a@b.de"}],
            "body": {"data": "!!!definitely-not-base64!!!"},
        },
        "internalDate": "1755000000000",
    }
    assert parse_gmail_message(raw).raw_content == ""


def test_html_only_message_drops_script_and_style_and_resolves_entities() -> None:
    html = (
        "<html><head><style>.x{color:red}</style>"
        "<script>var tracking=1;</script></head>"
        "<body><p>Guten Tag</p><p>Preis: 5&euro;</p></body></html>"
    )
    raw = _message(headers=[{"name": "From", "value": "a@b.de"}], body=html, mime="text/html")

    content = parse_gmail_message(raw).raw_content

    assert "Guten Tag" in content
    assert "5€" in content
    assert "color:red" not in content
    assert "tracking" not in content


def test_multipart_prefers_the_plain_text_part() -> None:
    raw = {
        "id": "gm-1",
        "internalDate": "1755000000000",
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [{"name": "From", "value": "a@b.de"}],
            "body": {},
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64("Nur Text")}},
                {"mimeType": "text/html", "body": {"data": _b64("<p>HTML</p>")}},
            ],
        },
    }
    assert parse_gmail_message(raw).raw_content == "Nur Text"


def test_attachments_metadata_is_collected_from_nested_parts() -> None:
    raw = {
        "id": "gm-1",
        "internalDate": "1755000000000",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [{"name": "From", "value": "a@b.de"}],
            "body": {},
            "parts": [
                {"mimeType": "text/plain", "filename": "", "body": {"data": _b64("Text")}},
                {
                    "mimeType": "application/pdf",
                    "filename": "angebot.pdf",
                    "body": {"size": 1234, "attachmentId": "att-1"},
                },
            ],
        },
    }

    attachments = parse_gmail_message(raw).attachments

    assert len(attachments) == 1
    assert attachments[0].filename == "angebot.pdf"
    assert attachments[0].size_bytes == 1234


def test_list_unsubscribe_header_is_captured_for_the_prefilter() -> None:
    raw = _message(
        headers=[
            {"name": "From", "value": "a@b.de"},
            {"name": "List-Unsubscribe", "value": "<https://example.com/u>"},
        ]
    )
    assert parse_gmail_message(raw).list_unsubscribe == "<https://example.com/u>"
