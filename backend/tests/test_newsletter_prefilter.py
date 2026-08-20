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


# --- language coverage --------------------------------------------------
#
# The vocabulary lists used to be hardcoded German (plus six English
# local-parts). The "needs human review" list in particular is a safety
# guard: a language gap there means a genuine inquiry gets quietly filed
# away as a newsletter, which is the expensive direction of the trade.


def test_an_english_inquiry_with_a_list_unsubscribe_header_is_not_filtered() -> None:
    """Regression: some CRM and helpdesk systems set List-Unsubscribe on
    genuine person-to-person mail. The German-only guard did not fire for
    an English request, so it was auto-filed as a newsletter."""
    assert (
        prefilter_newsletter_reason(
            subject="Request for quotation",
            sender_address="procurement@customer.com",
            body="Hello, could you send us a quotation for 200 units?",
            list_unsubscribe="<https://crm.example.com/unsubscribe>",
        )
        is None
    )


def test_an_english_purchase_order_is_not_filtered() -> None:
    assert (
        prefilter_newsletter_reason(
            subject="Purchase order 4711",
            sender_address="newsletter@customer.com",
            body="Please find our purchase order attached. Unsubscribe here.",
            list_unsubscribe=None,
        )
        is None
    )


def test_an_english_phishing_attempt_is_not_downgraded_to_newsletter() -> None:
    assert (
        prefilter_newsletter_reason(
            subject="Verify your account now",
            sender_address="marketing@phish.example",
            body="Your account suspended. Verify your credentials to unsubscribe.",
            list_unsubscribe="<https://phish.example/u>",
        )
        is None
    )


def test_additional_bulk_local_parts_are_recognised() -> None:
    """'nl@' and 'news@' are as common as 'newsletter@' and were missing."""
    for local_part in ("nl", "news", "mailings", "broadcast"):
        assert (
            prefilter_newsletter_reason(
                subject="Unsere Neuheiten im August",
                sender_address=f"{local_part}@shop.example",
                body="Viele neue Artikel im Sortiment. Hier abmelden.",
                list_unsubscribe=None,
            )
            is not None
        ), f"{local_part}@ wurde nicht als Bulk-Absender erkannt"


def test_english_boilerplate_counts_as_a_secondary_signal() -> None:
    assert (
        prefilter_newsletter_reason(
            subject="August highlights",
            sender_address="marketing@shop.example",
            body="Lots of new products this month. Manage preferences or opt out.",
            list_unsubscribe=None,
        )
        is not None
    )


def test_terms_are_matched_as_literals_not_as_regex(monkeypatch) -> None:
    """Configuration comes from an env var; a stray bracket in it must not
    take down every classification with a regex error."""
    from app.config import get_settings
    from app.services import newsletter_prefilter

    settings = get_settings()
    monkeypatch.setattr(settings, "newsletter_human_review_terms", "anfrage,(kaputt", raising=False)
    newsletter_prefilter._compile_any.cache_clear()

    try:
        result = prefilter_newsletter_reason(
            subject="Test",
            sender_address="newsletter@shop.example",
            body="Hier abmelden.",
            list_unsubscribe=None,
        )
        assert result is not None
    finally:
        newsletter_prefilter._compile_any.cache_clear()


def test_an_empty_term_list_disables_that_rule_without_crashing(monkeypatch) -> None:
    from app.config import get_settings
    from app.services import newsletter_prefilter

    settings = get_settings()
    monkeypatch.setattr(settings, "newsletter_boilerplate_terms", "", raising=False)
    newsletter_prefilter._compile_any.cache_clear()

    try:
        # Without boilerplate the secondary rule can never fire; the
        # protocol-level header rule still does.
        assert (
            prefilter_newsletter_reason(
                subject="Neuheiten",
                sender_address="newsletter@shop.example",
                body="Hier abmelden.",
                list_unsubscribe=None,
            )
            is None
        )
        assert (
            prefilter_newsletter_reason(
                subject="Neuheiten",
                sender_address="newsletter@shop.example",
                body="Hier abmelden.",
                list_unsubscribe="<https://shop.example/u>",
            )
            is not None
        )
    finally:
        newsletter_prefilter._compile_any.cache_clear()
