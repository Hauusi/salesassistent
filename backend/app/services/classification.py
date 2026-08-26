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
from app.services.llm_client import (
    call_tool,
    coerce_enum,
    coerce_float,
    coerce_str,
    get_anthropic_client,
)
from app.services.newsletter_prefilter import prefilter_newsletter_reason
from app.services.token_metrics import log_prompt_breakdown

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
            # Product-suggestion detection piggybacks on this same call
            # rather than a second Claude request - see
            # app/services/product_suggestion_service.py. All three are
            # optional and only meaningful together: leave every one empty
            # unless the mail contains both its own article number AND a
            # description/spec text for it (e.g. a supplier announcing a
            # new item) - a bare order/inquiry referencing an *existing*
            # article number with no description is not a detection.
            "detected_product_sku": {
                "type": "string",
                "description": (
                    "Artikelnummer/SKU, nur falls die Mail eine eigene Artikelnummer "
                    "UND einen zugehörigen Artikeltext/Beschreibung dazu enthält. Sonst leer lassen."
                ),
            },
            "detected_product_name": {
                "type": "string",
                "description": (
                    "Kurzer Produktname (max. 8 Worte) für detected_product_sku. "
                    "Nur gesetzt, wenn detected_product_sku gesetzt ist."
                ),
            },
            "detected_product_description": {
                "type": "string",
                "description": (
                    "Artikeltext/Beschreibung aus der Mail zu detected_product_sku. "
                    "Nur gesetzt, wenn detected_product_sku gesetzt ist."
                ),
            },
        },
        "required": ["wichtigkeits_kategorie", "typ", "confidence", "reasoning"],
    },
}

_SYSTEM_PROMPT = (
    "Du bist der Klassifikations-Assistent eines B2B-Sales-Postfachs. Ordne jede "
    "E-Mail per Tool-Aufruf 'classify_email' genau einer Wichtigkeits- und einer "
    "Typ-Kategorie zu. Sei konservativ bei spam_verdacht - nur eindeutig "
    "unerwünschte/betrügerische Mails, im Zweifel eher 'newsletter' oder 'information'. "
    "Enthält die Mail außerdem eine eigene Artikelnummer mit zugehörigem Artikeltext "
    "(z.B. eine Lieferantenankündigung eines neuen Produkts), fülle zusätzlich "
    "detected_product_sku/-name/-description; sonst lasse diese drei Felder leer."
)


@dataclass
class ClassificationResult:
    wichtigkeits_kategorie: WichtigkeitsKategorie
    typ: TypKategorie
    confidence: float
    reasoning: str
    suggested_case_title: str | None = None
    detected_product_sku: str | None = None
    detected_product_name: str | None = None
    detected_product_description: str | None = None


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
            get_settings().anthropic_classification_model,
            prefilter_reason,
        )
        return ClassificationResult(
            wichtigkeits_kategorie=WichtigkeitsKategorie.NEWSLETTER,
            typ=TypKategorie.KEINER,
            confidence=0.97,
            reasoning=prefilter_reason,
        )

    settings = get_settings()
    client = client or get_anthropic_client()
    user_message = _build_user_message(subject=subject, sender_address=sender_address, body=body)

    # Retry policy, forced-tool extraction and the "did we even get a tool
    # call" check all live in llm_client - see that module for what is
    # retried and what is not.
    data, response = await call_tool(
        client,
        call="classify_email",
        model=settings.anthropic_classification_model,
        max_tokens=1024,
        system=_SYSTEM_PROMPT,
        tool=_CLASSIFY_TOOL,
        user_message=user_message,
    )

    log_prompt_breakdown(
        "classify_email",
        model=settings.anthropic_classification_model,
        system=_SYSTEM_PROMPT,
        tools=[_CLASSIFY_TOOL],
        mail_content=user_message,
        usage=getattr(response, "usage", None),
    )

    # The tool schema constrains both category fields to an enum, but the
    # API does not hard-enforce that, so this is still untrusted input.
    # Coerce with a conservative fallback rather than raising: a single odd
    # response should cost one mail's classification accuracy, not the whole
    # poll batch (see app/workers/tasks.py). INFORMATION is the safe default
    # - it files the mail as searchable and visible without claiming an
    # answer is needed or hiding it as spam.
    return ClassificationResult(
        wichtigkeits_kategorie=coerce_enum(
            data.get("wichtigkeits_kategorie"),
            WichtigkeitsKategorie,
            WichtigkeitsKategorie.INFORMATION,
            field="wichtigkeits_kategorie",
            call="classify_email",
        ),
        typ=coerce_enum(
            data.get("typ"),
            TypKategorie,
            TypKategorie.KEINER,
            field="typ",
            call="classify_email",
        ),
        confidence=coerce_float(
            data.get("confidence"), 0.0, minimum=0.0, maximum=1.0,
            field="confidence", call="classify_email",
        ),
        reasoning=coerce_str(data.get("reasoning"), "(keine Begründung geliefert)", max_chars=1000),
        suggested_case_title=coerce_str(data.get("suggested_case_title"), max_chars=500) or None,
        detected_product_sku=coerce_str(data.get("detected_product_sku"), max_chars=100) or None,
        detected_product_name=coerce_str(data.get("detected_product_name"), max_chars=255) or None,
        detected_product_description=coerce_str(data.get("detected_product_description")) or None,
    )
