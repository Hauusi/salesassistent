"""Rule-based pre-check that skips the Claude classification call entirely
for mail that is unambiguously a newsletter/marketing bulk send - see
app/services/classification.py, which calls this before spending any
tokens.

Conservative by design: misclassifying a genuine customer inquiry (or a
phishing attempt) as 'newsletter' is far more expensive than the Haiku
call it would have saved, so every positive rule here is gated by a
negative "does this look like something a human needs to see" check. Any
mail that doesn't clearly clear a rule falls through to classify_email()'s
normal Claude call, unchanged - this module never produces a final answer
on its own, only an early exit.

The signals come in two kinds, and the distinction matters:

- The List-Unsubscribe header (RFC 2369/8058) is a *protocol* signal. It
  means the same thing in every language and is set by essentially every
  bulk sender and essentially no human mail client. It needs no
  configuration and is the primary rule.
- Sender local-parts and body phrases are *vocabulary*. They only ever
  recognise the languages and conventions they were written for. They ship
  with German and English defaults and are overridable per deployment
  (NEWSLETTER_* settings), because a hardcoded list silently limits the
  whole system to the language it was written in - and the "needs human
  review" list is a safety guard, where a language gap means a real
  inquiry gets filed away as a newsletter.
"""
from __future__ import annotations

import re
from functools import lru_cache

from app.config import get_settings


@lru_cache(maxsize=8)
def _compile_any(terms: tuple[str, ...]) -> re.Pattern[str] | None:
    """Builds one alternation from configured terms.

    Terms are treated as literals, not patterns: they come from
    configuration, and a stray "(" in an env var should not take down
    every classification with a regex error.
    """
    cleaned = [term.strip() for term in terms if term and term.strip()]
    if not cleaned:
        return None
    return re.compile("|".join(re.escape(term) for term in cleaned), re.IGNORECASE)


def _needs_human_review_pattern() -> re.Pattern[str] | None:
    return _compile_any(tuple(get_settings().newsletter_human_review_terms_list))


def _boilerplate_pattern() -> re.Pattern[str] | None:
    return _compile_any(tuple(get_settings().newsletter_boilerplate_terms_list))


def prefilter_newsletter_reason(
    *, subject: str | None, sender_address: str, body: str, list_unsubscribe: str | None,
) -> str | None:
    """Returns a short reasoning string if this mail can be labeled
    'newsletter' with confidence and without a Claude call; ``None`` if
    classify_email() should run its normal LLM call instead."""
    settings = get_settings()
    haystack = f"{subject or ''}\n{body}"

    # Safety gate: anything that looks like it needs a human is never
    # auto-filed, no matter how strong the bulk signals are. Covers genuine
    # business requests (don't bury an Anfrage under 'newsletter') and
    # phishing/urgency patterns (don't downgrade a spam_verdacht candidate
    # to a quietly-filed-away newsletter).
    human_review = _needs_human_review_pattern()
    if human_review is not None and human_review.search(haystack):
        return None

    # Primary signal: a protocol header, language-independent.
    if list_unsubscribe and list_unsubscribe.strip():
        return "Regelbasiert erkannt: List-Unsubscribe-Header vorhanden, kein Claude-Aufruf."

    # Secondary signal, for senders that omit the header: a bulk-mail
    # sender local-part *plus* matching footer boilerplate. Either alone is
    # too weak to risk a false positive.
    local_part = sender_address.split("@", 1)[0].lower() if "@" in sender_address else ""
    sender_is_bulk = local_part in settings.newsletter_bulk_local_parts_set

    boilerplate = _boilerplate_pattern()
    has_boilerplate = boilerplate is not None and boilerplate.search(haystack) is not None

    if sender_is_bulk and has_boilerplate:
        return (
            "Regelbasiert erkannt: Absendermuster + Newsletter-Textbausteine, "
            "kein Claude-Aufruf."
        )
    return None
