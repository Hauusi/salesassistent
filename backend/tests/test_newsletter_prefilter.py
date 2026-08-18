"""Unit tests for the rule-based newsletter pre-check itself (no Anthropic
client involved) - see app.services.newsletter_prefilter and
tests/test_classification.py for the classify_email()-level integration
tests (asserting the LLM call is actually skipped/not skipped)."""
from __future__ import annotations

from app.services.newsletter_prefilter import prefilter_newsletter_reason
from tests.fixtures.emails import ANFRAGE_MAIL, BESTELLUNG_MAIL, INFORMATION_MAIL, NEWSLETTER_MAIL, SPAM_MAIL


def test_list_unsubscribe_header_is_recognized_as_newsletter() -> None:
    reason = prefilter_newsletter_reason(
        subject="Ihr Monats-Update",
        sender_address="info@some-industry-blog.example",
        body="Hier die neuesten Beiträge aus unserem Blog.",
        list_unsubscribe="<mailto:unsubscribe@some-industry-blog.example>",
    )
    assert reason is not None
    assert "List-Unsubscribe" in reason


def test_sender_pattern_plus_boilerplate_is_recognized_as_newsletter() -> None:
    reason = prefilter_newsletter_reason(
        subject=NEWSLETTER_MAIL["subject"],
        sender_address=NEWSLETTER_MAIL["sender_address"],
        body=NEWSLETTER_MAIL["body"],
        list_unsubscribe=None,
    )
    assert reason is not None


def test_ambiguous_mail_without_signals_falls_through_to_llm() -> None:
    """No List-Unsubscribe header, no bulk-sender pattern, no boilerplate -
    must defer to the normal Claude classification call."""
    reason = prefilter_newsletter_reason(
        subject=ANFRAGE_MAIL["subject"],
        sender_address=ANFRAGE_MAIL["sender_address"],
        body=ANFRAGE_MAIL["body"],
        list_unsubscribe=None,
    )
    assert reason is None


def test_real_inquiry_is_never_filtered_even_with_list_unsubscribe_header() -> None:
    """Conservative-by-design guard: even a hard bulk-mail signal must not
    override an unambiguous business request in the content (e.g. a
    marketing-platform-sent survey that also contains an actual order)."""
    reason = prefilter_newsletter_reason(
        subject="Ihr Angebot ist da - jetzt bestellen!",
        sender_address="newsletter@shop.example",
        body="Wir möchten hiermit 10 Stück Winkelverbinder WV-12 bestellen. Bitte um Auftragsbestätigung.",
        list_unsubscribe="<mailto:unsubscribe@shop.example>",
    )
    assert reason is None


def test_bulk_sender_pattern_alone_without_boilerplate_is_not_enough() -> None:
    """Sender pattern is only a secondary/weak signal - without the
    List-Unsubscribe header AND without matching footer boilerplate, it
    must not be enough on its own to skip the LLM call."""
    reason = prefilter_newsletter_reason(
        subject="Rückfrage zu Ihrer letzten Anfrage",
        sender_address="marketing@partner-firma.example",
        body="Kurze Rückfrage zu den Spezifikationen, die Sie uns letzte Woche geschickt haben.",
        list_unsubscribe=None,
    )
    assert reason is None


def test_spam_candidate_is_not_downgraded_to_newsletter() -> None:
    """A phishing mail must keep going through classify_email's
    spam_verdacht path, even if it happened to carry bulk-mail signals -
    urgency/credential-harvesting language always defers to the LLM."""
    reason = prefilter_newsletter_reason(
        subject=SPAM_MAIL["subject"],
        sender_address=SPAM_MAIL["sender_address"],
        body=SPAM_MAIL["body"],
        list_unsubscribe="<mailto:unsubscribe@paypa1-secure.ru>",
    )
    assert reason is None


def test_bestellung_and_information_mails_are_not_filtered() -> None:
    for fixture in (BESTELLUNG_MAIL, INFORMATION_MAIL):
        reason = prefilter_newsletter_reason(
            subject=fixture["subject"],
            sender_address=fixture["sender_address"],
            body=fixture["body"],
            list_unsubscribe=None,
        )
        assert reason is None, f"{fixture['subject']!r} must not be auto-filtered as newsletter"
