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


def test_strip_quoted_reply_keeps_body_that_starts_with_a_quote_line() -> None:
    """Regression: the bare ">" marker matches at position 0 for a
    top-posted forward or an inline reply that opens by quoting. Trimming
    there produced an empty body, which silently reached the classifier and
    the draft generator as "no content at all"."""
    body = "> Angebot für 5 Stück Aluminiumprofil\n\nHallo, bitte um Preis und Lieferzeit."
    assert strip_quoted_reply(body) == body


def test_strip_quoted_reply_keeps_body_that_starts_with_a_nested_quote() -> None:
    body = ">>> Weitergeleitete Nachricht\nGuten Tag, wir brauchen 200 Schrauben M8."
    assert strip_quoted_reply(body) == body


def test_strip_quoted_reply_keeps_body_whose_quote_header_is_the_first_line() -> None:
    body = "Am 12.03.2024 um 10:15 schrieb Max Mustermann <max@example.com>:\n> alter Text"
    # Nothing of the sender's own is above the marker - keep the raw text
    # rather than handing an empty string to the LLM.
    assert strip_quoted_reply(body) == body


def test_strip_quoted_reply_still_trims_when_enough_content_remains() -> None:
    body = (
        "Guten Tag, wir benötigen ein Angebot über 200 Schrauben M8.\n\n"
        "Am 12.03.2024 um 10:15 schrieb Max <max@example.com>:\n"
        "> alter Text"
    )
    assert strip_quoted_reply(body) == "Guten Tag, wir benötigen ein Angebot über 200 Schrauben M8."


def test_strip_quoted_reply_returns_short_body_untouched_when_there_is_no_quote() -> None:
    # A genuinely short mail must not be confused with a failed trim.
    assert strip_quoted_reply("Danke!") == "Danke!"
