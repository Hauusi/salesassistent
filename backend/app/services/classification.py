"""LLM-based classification of incoming mail (concept doc section 5.3).

Classifies every mail along two independent axes:
- Wichtigkeit: antwort_erforderlich | information | newsletter | spam_verdacht
- Typ (orthogonal): bestellung | anfrage | keiner

Uses Claude's forced tool-use to get reliable structured output instead of
parsing free text.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from anthropic import AsyncAnthropic

from app.config import get_settings
from app.models.enums import TypKategorie, WichtigkeitsKategorie
from app.services.email_text import strip_quoted_reply
from app.services.llm_client import get_anthropic_client
from app.services.newsletter_prefilter import prefilter_newsletter_reason
from app.services.token_metrics import log_prompt_breakdown

settings = get_settings()
logger = logging.getLogger("app.llm.tokens")

# Kept information-dense on purpose (one line per enum value covers the
# distinguishing signal a forced tool call needs) - trimmed to remove
# filler words only. See app/services/token_metrics.py for what this costs
# per call; cutting the substance here to save a few dozen tokens would
# risk misclassification, which is far more expensive than the tokens.
_CLASSIFY_TOOL = {
    "name": "classify_email",
    "description": "Klassifiziert eine E-Mail in Wichtigkeits- und Typ-Kategorie.",
    "input_schema": {
        "type": "object",
        "properties": {
            "wichtigkeits_kategorie": {
                "type": "string",
                "enum": [e.value for e in WichtigkeitsKategorie],
                "description": (
                    "antwort_erforderlich: Absender erwartet inhaltliche Antwort (Frage, "
                    "Bitte, offener Vorgang). "
                    "information: reine Mitteilung ohne Antworterwartung (Bestätigung, "
                    "Status, FYI), aber ablagewürdig. "
                    "newsletter: wiederkehrender Massenversand ohne individuellen Bezug. "
                    "spam_verdacht: unerwünscht/verdächtig (Phishing, Betrug, Werbung ohne "
                    "Opt-in)."
                ),
            },
            "typ": {
                "type": "string",
                "enum": [e.value for e in TypKategorie],
                "description": (
                    "bestellung: konkrete Bestellung/Auftrag. "
                    "anfrage: Anfrage nach Angebot/Produkt/Leistung, noch keine Bestellung. "
                    "keiner: trifft keines von beiden zu."
                ),
            },
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "Konfidenz zwischen 0 und 1.",
            },
            "reasoning": {
                "type": "string",
                "description": "Sehr knappe Begründung auf Deutsch, max. 15 Worte.",
            },
            "suggested_case_title": {
                "type": "string",
                "description": "Titel (max. 8 Worte) für diese Mail, falls noch kein Case existiert.",
            },
        },
        "required": ["wichtigkeits_kategorie", "typ", "confidence", "reasoning"],
    },
}

_SYSTEM_PROMPT = (
    "Du bist der Klassifikations-Assistent eines B2B-Sales-Postfachs. Ordne jede "
    "E-Mail per Tool-Aufruf 'classify_email' genau einer Wichtigkeits- und einer "
    "Typ-Kategorie zu. Sei konservativ bei spam_verdacht - nur eindeutig "
    "unerwünschte/betrügerische Mails, im Zweifel eher 'newsletter' oder 'information'."
)


@dataclass
class ClassificationResult:
    wichtigkeits_kategorie: WichtigkeitsKategorie
    typ: TypKategorie
    confidence: float
    reasoning: str
    suggested_case_title: str | None = None


def _build_user_message(*, subject: str | None, sender_address: str, body: str) -> str:
    # Classification only needs the new message, not the quoted thread
    # history Gmail includes in the plain-text body - stripping it first
    # means the 6000-char safety cap is spent on actual new content instead
    # of a mail's own copy of everything that came before it.
    truncated_body = strip_quoted_reply(body)[:6000]
    return (
        f"Absender: {sender_address}\n"
        f"Betreff: {subject or '(kein Betreff)'}\n\n"
        f"Inhalt:\n{truncated_body}"
    )


async def classify_email(
    *,
    subject: str | None,
    sender_address: str,
    body: str,
    list_unsubscribe: str | None = None,
    client: AsyncAnthropic | None = None,
) -> ClassificationResult:
    # Cheap, regex-only pre-check before spending any tokens - see
    # app/services/newsletter_prefilter.py for the (conservative) rules.
    # Only ever short-circuits to 'newsletter'; anything it isn't sure
    # about falls through to the Claude call below unchanged.
    prefilter_reason = prefilter_newsletter_reason(
        subject=subject, sender_address=sender_address, body=body, list_unsubscribe=list_unsubscribe
    )
    if prefilter_reason is not None:
        logger.info(
            "claude_call_skipped call=classify_email model=%s reason=%s",
            settings.anthropic_classification_model,
            prefilter_reason,
        )
        return ClassificationResult(
            wichtigkeits_kategorie=WichtigkeitsKategorie.NEWSLETTER,
            typ=TypKategorie.KEINER,
            confidence=0.97,
            reasoning=prefilter_reason,
        )

    client = client or get_anthropic_client()
    user_message = _build_user_message(subject=subject, sender_address=sender_address, body=body)

    response = await client.messages.create(
        model=settings.anthropic_classification_model,
        max_tokens=1024,
        # A breakpoint on the (only) system block also covers the tools
        # block, since tools render before system - see
        # https://docs.claude.com/en/docs/build-with-claude/prompt-caching.
        # Both are static/identical across every classify_email call. Note:
        # system+tool schema here (~500 tokens) is currently below Haiku's
        # ~4096-token minimum cacheable prefix, so this is a no-op today
        # (no error, no charge - cache_creation_input_tokens stays 0), not
        # a current cost saving. It's forward-compatible for free: as soon
        # as this prompt grows (few-shot examples, tenant-specific rules),
        # it starts getting cached with zero further code changes.
        system=[{"type": "text", "text": _SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        tools=[_CLASSIFY_TOOL],
        tool_choice={"type": "tool", "name": "classify_email"},
        messages=[{"role": "user", "content": user_message}],
    )

    log_prompt_breakdown(
        "classify_email",
        model=settings.anthropic_classification_model,
        system=_SYSTEM_PROMPT,
        tools=[_CLASSIFY_TOOL],
        mail_content=user_message,
        usage=getattr(response, "usage", None),
    )

    tool_use = next((block for block in response.content if getattr(block, "type", None) == "tool_use"), None)
    if tool_use is None:
        raise ValueError("Claude hat kein 'classify_email' Tool-Ergebnis zurückgegeben.")
    data = tool_use.input

    return ClassificationResult(
        wichtigkeits_kategorie=WichtigkeitsKategorie(data["wichtigkeits_kategorie"]),
        typ=TypKategorie(data["typ"]),
        confidence=float(data["confidence"]),
        reasoning=data["reasoning"],
        suggested_case_title=data.get("suggested_case_title"),
    )
