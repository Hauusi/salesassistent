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
from app.services.email_text import strip_quoted_reply
from app.services.llm_client import get_anthropic_client
from app.services.product_search import format_products_for_prompt, search_products
from app.services.token_metrics import log_prompt_breakdown

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

# Trimmed for filler only - the anti-hallucination and product-grounding
# rules below are safety-critical (fabricated prices/deadlines/promises in
# a customer-facing draft are far costlier than the tokens), so their
# substance is kept even though the wording is tighter.
_SYSTEM_PROMPT = (
    "Du bist der Entwurfs-Assistent eines Vertriebsteams: Du schreibst Antwortentwürfe "
    "auf Kunden-E-Mails, die vor dem Versand von einem Menschen geprüft und freigegeben "
    "werden. Nutze den mitgelieferten Verlauf (RAG) als Kontext. Erfinde keine Fakten "
    "(Preise, Liefertermine, Zusagen) - was nicht im Kontext steht, kennzeichne im "
    "Entwurf als noch zu prüfen. Werden passende Produkte aus der Wissensbasis "
    "mitgegeben, nutze deren konkrete Preise, Verfügbarkeit und Specs direkt in der "
    "Antwort statt pauschal nachzufragen - aber nur exakt diese Werte, nichts darüber "
    "hinaus erfinden. Fehlt ein passendes Produkt, sag das nicht explizit, sondern "
    "beantworte die Anfrage so gut wie mit dem übrigen Kontext möglich und bitte bei "
    "Bedarf um Präzisierung."
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
        # Each stored mail's raw_content can itself carry the thread quoted
        # below it (see app.services.email_text) - stripping that before
        # truncating means the 2000-char budget per history mail is spent
        # on that mail's own new content, not a repeat of mails already
        # included elsewhere in this same RAG context.
        body = strip_quoted_reply(msg.raw_content)[:2000]
        parts.append(
            f"--- Mail vom {msg.received_at.isoformat()} von {msg.sender_address} ---\n"
            f"Betreff: {msg.subject or '(kein Betreff)'}\n"
            f"{body}"
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

    # Strip the quoted thread from the new mail too - it's already covered
    # by context_text above (which pulls the actual prior mails from the
    # DB), so re-sending it a second time as part of this mail's own body
    # would be pure duplication.
    new_mail_body = strip_quoted_reply(email.raw_content)[:6000]

    user_message = (
        f"Bisheriger Verlauf (RAG-Kontext, chronologisch):\n{context_text}\n\n"
        f"---\n\nZu beantwortende neue Mail von {email.sender_address}:\n"
        f"Betreff: {email.subject or '(kein Betreff)'}\n\n{new_mail_body}"
        f"{product_context_block}"
    )

    response = await client.messages.create(
        model=settings.anthropic_model,
        max_tokens=1500,
        # See classification.py for the caching rationale/tradeoffs - same
        # pattern here. This system+tool prefix (~350-400 tokens) is below
        # Sonnet's 1024-token cacheable minimum today, so it's a no-op right
        # now rather than a current saving; it activates automatically if
        # this prompt grows later.
        system=[{"type": "text", "text": _SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        tools=[_DRAFT_TOOL],
        tool_choice={"type": "tool", "name": "generate_reply_draft"},
        messages=[{"role": "user", "content": user_message}],
    )

    log_prompt_breakdown(
        "generate_draft",
        model=settings.anthropic_model,
        system=_SYSTEM_PROMPT,
        tools=[_DRAFT_TOOL],
        mail_content=f"Betreff: {email.subject or ''}\n\n{new_mail_body}",
        product_context=product_context_block,
        other_context=context_text,
        usage=getattr(response, "usage", None),
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
