"""Reply-draft generation for antwort_erforderlich mails, with RAG context
pulled from prior correspondence with the same contact/case.

The generated draft always lands in Draft.status = ENTWURF and is never
sent automatically - see app/services/pipeline.py and
app/api/routes/drafts.py for the approval gate.
"""
from __future__ import annotations

import uuid

from anthropic import AsyncAnthropic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.email_message import EmailMessage
from app.services.llm_client import get_anthropic_client

settings = get_settings()

_DRAFT_TOOL = {
    "name": "generate_reply_draft",
    "description": "Erstellt einen Antwortentwurf auf eine eingehende Geschäfts-E-Mail.",
    "input_schema": {
        "type": "object",
        "properties": {
            "subject": {"type": "string", "description": "Betreff der Antwort."},
            "body": {
                "type": "string",
                "description": (
                    "Vollständiger Antworttext auf Deutsch, professionell, freundlich, "
                    "auf den Punkt. Ohne Grußformel-Signatur mit Firmendaten, die kommt "
                    "aus dem Mailsystem."
                ),
            },
        },
        "required": ["subject", "body"],
    },
}

_SYSTEM_PROMPT = (
    "Du bist der Entwurfs-Assistent eines Vertriebsteams. Du erstellst Antwortentwürfe "
    "auf eingehende Kunden-E-Mails, die anschließend von einem Menschen geprüft, "
    "bearbeitet und erst nach expliziter Freigabe versendet werden. Nutze den "
    "bereitgestellten Verlauf als Kontext (RAG), erfinde keine Fakten (Preise, "
    "Liefertermine, Zusagen), die nicht aus dem Kontext hervorgehen - weise stattdessen "
    "im Entwurf darauf hin, dass das noch zu prüfen ist."
)


async def _gather_rag_context(
    db: AsyncSession, *, tenant_id: uuid.UUID, contact_id: uuid.UUID | None, case_id: uuid.UUID | None,
    exclude_email_id: uuid.UUID, limit: int = 5,
) -> list[EmailMessage]:
    if contact_id is None and case_id is None:
        return []

    stmt = (
        select(EmailMessage)
        .where(EmailMessage.tenant_id == tenant_id, EmailMessage.id != exclude_email_id)
        .order_by(EmailMessage.received_at.desc())
        .limit(limit)
    )
    if case_id is not None:
        stmt = stmt.where(EmailMessage.case_id == case_id)
    elif contact_id is not None:
        stmt = stmt.where(EmailMessage.contact_id == contact_id)

    result = await db.execute(stmt)
    return list(result.scalars().all())


def _format_context(history: list[EmailMessage]) -> str:
    if not history:
        return "(kein bisheriger Verlauf mit diesem Kontakt/Case gefunden)"
    parts = []
    for msg in reversed(history):  # chronological
        parts.append(
            f"--- Mail vom {msg.received_at.isoformat()} von {msg.sender_address} ---\n"
            f"Betreff: {msg.subject or '(kein Betreff)'}\n"
            f"{msg.raw_content[:2000]}"
        )
    return "\n\n".join(parts)


async def generate_draft(
    db: AsyncSession,
    *,
    email: EmailMessage,
    client: AsyncAnthropic | None = None,
) -> tuple[str, str, str]:
    """Returns (subject, body, rag_context_summary)."""
    client = client or get_anthropic_client()

    history = await _gather_rag_context(
        db, tenant_id=email.tenant_id, contact_id=email.contact_id, case_id=email.case_id,
        exclude_email_id=email.id,
    )
    context_text = _format_context(history)

    user_message = (
        f"Bisheriger Verlauf (RAG-Kontext, chronologisch):\n{context_text}\n\n"
        f"---\n\nZu beantwortende neue Mail von {email.sender_address}:\n"
        f"Betreff: {email.subject or '(kein Betreff)'}\n\n{email.raw_content[:6000]}"
    )

    response = await client.messages.create(
        model=settings.anthropic_model,
        max_tokens=1500,
        system=_SYSTEM_PROMPT,
        tools=[_DRAFT_TOOL],
        tool_choice={"type": "tool", "name": "generate_reply_draft"},
        messages=[{"role": "user", "content": user_message}],
    )

    tool_use = next((block for block in response.content if getattr(block, "type", None) == "tool_use"), None)
    if tool_use is None:
        raise ValueError("Claude hat kein 'generate_reply_draft' Tool-Ergebnis zurückgegeben.")
    data = tool_use.input

    rag_summary = f"{len(history)} vorherige Mail(s) als Kontext verwendet." if history else "Kein RAG-Kontext verfügbar."
    return data["subject"], data["body"], rag_summary
