"""LLM-based classification of incoming mail (concept doc section 5.3).

Classifies every mail along two independent axes:
- Wichtigkeit: antwort_erforderlich | information | newsletter | spam_verdacht
- Typ (orthogonal): bestellung | anfrage | keiner

Uses Claude's forced tool-use to get reliable structured output instead of
parsing free text.
"""
from __future__ import annotations

from dataclasses import dataclass

from anthropic import AsyncAnthropic

from app.config import get_settings
from app.models.enums import TypKategorie, WichtigkeitsKategorie
from app.services.email_text import strip_quoted_reply
from app.services.llm_client import get_anthropic_client

settings = get_settings()

_CLASSIFY_TOOL = {
    "name": "classify_email",
    "description": "Klassifiziert eine eingehende Geschäfts-E-Mail in eine Wichtigkeits- und eine Typ-Kategorie.",
    "input_schema": {
        "type": "object",
        "properties": {
            "wichtigkeits_kategorie": {
                "type": "string",
                "enum": [e.value for e in WichtigkeitsKategorie],
                "description": (
                    "antwort_erforderlich: der Absender erwartet erkennbar eine inhaltliche "
                    "Antwort (Frage, Bitte, offener Vorgang). "
                    "information: reine Mitteilung ohne Antworterwartung (Bestätigung, "
                    "Statusupdate, FYI), aber inhaltlich relevant und ablagewürdig. "
                    "newsletter: wiederkehrender Massenversand/Marketing-Mailing, kein "
                    "individueller Bezug zum Empfänger. "
                    "spam_verdacht: unerwünschte/verdächtige Mail (Phishing, Betrug, "
                    "irrelevante Massenwerbung ohne Opt-in-Bezug)."
                ),
            },
            "typ": {
                "type": "string",
                "enum": [e.value for e in TypKategorie],
                "description": (
                    "bestellung: enthält eine konkrete Bestellung/einen Auftrag. "
                    "anfrage: enthält eine Anfrage nach einem Angebot/Produkt/einer "
                    "Leistung, ohne bereits eine Bestellung zu sein. "
                    "keiner: trifft keines von beiden zu."
                ),
            },
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "Konfidenz der Klassifikation zwischen 0 und 1.",
            },
            "reasoning": {
                "type": "string",
                "description": "Kurze Begründung auf Deutsch, 1-2 Sätze.",
            },
            "suggested_case_title": {
                "type": "string",
                "description": (
                    "Kurzer, prägnanter Titel (max. 8 Worte) für das Thema dieser Mail, "
                    "falls dafür noch kein bestehender Case existiert."
                ),
            },
        },
        "required": ["wichtigkeits_kategorie", "typ", "confidence", "reasoning"],
    },
}

_SYSTEM_PROMPT = (
    "Du bist der Klassifikations-Assistent eines B2B-Sales-Postfachs. "
    "Ordne jede eingehende E-Mail exakt einer Wichtigkeits-Kategorie und einer "
    "Typ-Kategorie zu, indem du ausschließlich das Tool 'classify_email' aufrufst. "
    "Sei konservativ bei spam_verdacht - markiere nur eindeutig unerwünschte oder "
    "betrügerische Mails so, im Zweifel eher 'newsletter' oder 'information'."
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
    client: AsyncAnthropic | None = None,
) -> ClassificationResult:
    client = client or get_anthropic_client()

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
        messages=[
            {
                "role": "user",
                "content": _build_user_message(subject=subject, sender_address=sender_address, body=body),
            }
        ],
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
