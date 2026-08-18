"""Tests for app.services.email_text.strip_quoted_reply - the quote-trail
trimming used to keep classification/draft-generation prompts down to the
actual new message (see app/services/classification.py,
app/services/draft_generation.py)."""
from __future__ import annotations

from app.services.email_text import strip_quoted_reply


def test_strip_quoted_reply_cuts_gmail_style_german_quote_header() -> None:
    text = (
        "Klar, das passt fuer uns.\n\n"
        "Am Mo., 12. Aug. 2026 um 10:15 schrieb Julia Bauer <julia@musterkunde.de>:\n"
        "> Koennen wir den Termin auf Montag legen?\n"
        "> Viele Gruesse\n"
    )
    assert strip_quoted_reply(text) == "Klar, das passt fuer uns."


def test_strip_quoted_reply_cuts_gmail_style_english_quote_header() -> None:
    text = (
        "Sounds good, thanks!\n\n"
        "On Mon, 12 Aug 2026 at 10:15, Julia Bauer <julia@example.com> wrote:\n"
        "> Can we move the call to Monday?\n"
    )
    assert strip_quoted_reply(text) == "Sounds good, thanks!"


def test_strip_quoted_reply_cuts_outlook_style_header_block() -> None:
    text = (
        "Anbei wie besprochen.\n\n"
        "Von: Max Mustermann <max@example.com>\n"
        "Gesendet: Montag, 10. August 2026 09:00\n"
        "An: Julia Bauer\n"
        "Betreff: AW: Angebot\n\n"
        "Bitte um Rueckmeldung."
    )
    assert strip_quoted_reply(text) == "Anbei wie besprochen."


def test_strip_quoted_reply_cuts_bare_quote_lines_without_header() -> None:
    text = "Ja, gerne.\n> Frueherer Text\n> noch mehr frueherer Text"
    assert strip_quoted_reply(text) == "Ja, gerne."


def test_strip_quoted_reply_leaves_text_without_quote_markers_untouched() -> None:
    text = "Eine ganz normale Anfrage ohne jeden zitierten Verlauf."
    assert strip_quoted_reply(text) == text


def test_strip_quoted_reply_handles_empty_string() -> None:
    assert strip_quoted_reply("") == ""
