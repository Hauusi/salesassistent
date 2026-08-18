"""Rule-based pre-check that skips the Claude classification call
entirely for mail that is unambiguously a newsletter/marketing bulk send
- see app/services/classification.py, which calls this before spending
any tokens.

Conservative by design: misclassifying a genuine customer inquiry (or a
phishing attempt) as 'newsletter' is far more expensive than the Haiku
call it would have saved, so every positive rule here is gated by a
negative "does this look like something a human needs to see" check.
Any mail that doesn't clearly clear a rule falls through to
classify_email()'s normal Claude call, unchanged - this module never
produces a final answer on its own, only an early exit.
"""
from __future__ import annotations

import re

# Local-parts used essentially exclusively by bulk-send systems/ESPs
# (Mailchimp, HubSpot, Sendinblue, ...), never by a human composing a
# genuine business inquiry/order in their normal mail client. Deliberately
# does NOT include "no-reply@"/"noreply@"/"donotreply@" - those are just as
# commonly used for transactional mail (order confirmations, password
# resets, shipping notices) that must NOT be swept into 'newsletter'.
_BULK_SENDER_LOCAL_PARTS = {
    "newsletter", "newsletters", "marketing", "mailer", "campaign", "bulkmail",
}

# Boilerplate phrases that show up in the footer of essentially every
# marketing/newsletter send, German and English - used only as a
# *secondary* signal (see below), never on its own.
_NEWSLETTER_BOILERPLATE_RE = re.compile(
    r"abmelden|abbestellen|unsubscribe|"
    r"im browser (ansehen|anzeigen)|view (this email|in.browser)|"
    r"sie erhalten diese (e-?mail|nachricht)",
    re.IGNORECASE,
)

# Any of these anywhere in subject/body means "a human probably needs to
# act on this" - never auto-classify as newsletter if present, no matter
# how strong the bulk-mail signals are. Covers both genuine business
# requests (don't bury a real Anfrage/Bestellung under 'newsletter') and
# phishing/urgency patterns (don't downgrade a spam_verdacht candidate to
# a quietly-filed-away newsletter).
_NEEDS_HUMAN_REVIEW_RE = re.compile(
    r"anfrage|angebot|bestellung|bestellen|auftrag|rechnung|lieferzeit|"
    r"reklamation|storno|k[uü]ndigung|dringend|urgent|verify your|"
    r"passwort|password|login|kreditkarte|credit card|konto gesperrt",
    re.IGNORECASE,
)


def prefilter_newsletter_reason(
    *, subject: str | None, sender_address: str, body: str, list_unsubscribe: str | None,
) -> str | None:
    """Returns a short reasoning string if this mail can be labeled
    'newsletter' with confidence and without a Claude call; ``None`` if
    classify_email() should run its normal LLM call instead."""
    haystack = f"{subject or ''}\n{body}"
    if _NEEDS_HUMAN_REVIEW_RE.search(haystack):
        return None

    local_part = sender_address.split("@", 1)[0].lower() if "@" in sender_address else ""

    # Primary signal: the List-Unsubscribe header is specifically for bulk
    # mail (RFC 2369/8058) and essentially never set by a normal mail
    # client on person-composed correspondence.
    has_list_unsubscribe = bool(list_unsubscribe and list_unsubscribe.strip())

    # Secondary signal, used when the header is missing (some senders
    # don't set it): a newsletter-specific sender local-part *plus*
    # matching footer boilerplate in the body - either alone is too weak
    # on its own to risk a false positive.
    sender_is_bulk = local_part in _BULK_SENDER_LOCAL_PARTS
    has_boilerplate = bool(_NEWSLETTER_BOILERPLATE_RE.search(haystack))

    if has_list_unsubscribe:
        return "Regelbasiert erkannt: List-Unsubscribe-Header vorhanden, kein Claude-Aufruf."
    if sender_is_bulk and has_boilerplate:
        return (
            "Regelbasiert erkannt: Absendermuster + Newsletter-Textbausteine "
            "(z.B. 'abmelden'), kein Claude-Aufruf."
        )
    return None
