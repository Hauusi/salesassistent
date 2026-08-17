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
from app.models.enums import TypKategorie
from app.models.product import Product
from app.services.llm_client import get_anthropic_client
from app.services.product_search import format_products_for_prompt, search_products

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
    "im Entwurf darauf hin, dass das noch zu prüfen ist. Wenn dir passende Produkte aus "
    "der Produkt-Wissensbasis mitgegeben werden, nutze deren konkrete Preise, "
    "Verfügbarkeit und Specs direkt in der Antwort, statt pauschal nach weiteren Details "
    "zu fragen - aber nur für exakt die dort genannten Werte, erfinde nichts darüber "
    "hinaus. Wenn kein passendes Produkt in der Wissensbasis gefunden wurde, sag das "
    "nicht explizit, sondern beantworte die Anfrage so gut wie mit dem übrigen Kontext "
    "möglich und bitte bei Bedarf um Präzisierung."
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

    # Angebotsanfragen (typ=anfrage) get product grounding: search the
    # catalog for keyword matches in the request and hand the results to
    # the model so it can quote concrete prices/specs instead of just
    # asking the customer to wait for a human to look them up.
    matched_products: list[Product] = []
    product_context_block = ""
    if email.typ == TypKategorie.ANFRAGE:
        matched_products = await search_products(
            db,
            tenant_id=email.tenant_id,
            query_text=f"{email.subject or ''}\n{email.raw_content}",
        )
        if matched_products:
            product_context_block = (
                "\n\n---\n\nPassende Produkte aus der Produkt-Wissensbasis "
                f"(nutze diese konkreten Werte in der Antwort):\n{format_products_for_prompt(matched_products)}"
            )

    user_message = (
        f"Bisheriger Verlauf (RAG-Kontext, chronologisch):\n{context_text}\n\n"
        f"---\n\nZu beantwortende neue Mail von {email.sender_address}:\n"
        f"Betreff: {email.subject or '(kein Betreff)'}\n\n{email.raw_content[:6000]}"
        f"{product_context_block}"
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
    if email.typ == TypKategorie.ANFRAGE:
        if matched_products:
            names = ", ".join(p.name for p in matched_products)
            rag_summary += f" Produkte aus der Wissensbasis herangezogen: {names}."
        else:
            rag_summary += " Keine passenden Produkte in der Wissensbasis gefunden."

    return data["subject"], data["body"], rag_summary
