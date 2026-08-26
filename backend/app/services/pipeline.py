"""End-to-end processing of one fetched mail: contact/case assignment,
classification, and the per-category action logic from concept doc 5.3.

    newsletter           -> label only, no further action
    antwort_erforderlich -> generate draft (RAG), status = wartet_auf_freigabe
    information          -> file structured, searchable, status = abgelegt
    bestellung / anfrage -> additionally flagged via `typ` (orthogonal)
    spam_verdacht        -> flagged, hidden from default view, never deleted

The per-category behaviour is expressed as two lookup tables plus one
branch for the single category that does real work, rather than a chain of
`elif`s: adding a category should be a table entry, not another arm.

Every step that makes an automated decision is written to ActionLog.
`process_incoming_email` owns the transaction and is idempotent per
(mailbox, gmail_message_id).
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.attachment import Attachment
from app.models.case import Case, CaseContact
from app.models.contact import Contact
from app.models.draft import Draft
from app.models.email_message import EmailMessage
from app.models.enums import ActionActor, EmailStatus, WichtigkeitsKategorie
from app.models.limits import fit
from app.models.mailbox import Mailbox
from app.services import case_matching, classification, embeddings
from app.services.action_log_service import log_action
from app.services.draft_generation import generate_draft
from app.services.gmail_client import FetchedEmail
from app.services.product_suggestion_service import maybe_create_suggestion

logger = logging.getLogger(__name__)

_STATUS_BY_WICHTIGKEIT = {
    WichtigkeitsKategorie.NEWSLETTER: EmailStatus.ERLEDIGT,
    WichtigkeitsKategorie.ANTWORT_ERFORDERLICH: EmailStatus.WARTET_AUF_FREIGABE,
    WichtigkeitsKategorie.INFORMATION: EmailStatus.ABGELEGT,
    WichtigkeitsKategorie.SPAM_VERDACHT: EmailStatus.AUSGEBLENDET,
}

# What each category records in the audit trail. antwort_erforderlich is
# absent on purpose: it logs `draft_generated` against the draft it creates
# (see _generate_draft_for), which is a different entity, not a different
# spelling of the same log line.
_FILING_ACTION_BY_WICHTIGKEIT = {
    WichtigkeitsKategorie.SPAM_VERDACHT: "hidden_as_spam_verdacht",
    WichtigkeitsKategorie.INFORMATION: "filed_as_information",
    WichtigkeitsKategorie.NEWSLETTER: "labeled_newsletter",
}


async def _already_processed(db: AsyncSession, *, mailbox: Mailbox, gmail_message_id: str) -> bool:
    existing = await db.execute(
        select(EmailMessage.id).where(
            EmailMessage.mailbox_id == mailbox.id,
            EmailMessage.gmail_message_id == gmail_message_id,
        )
    )
    return existing.scalar_one_or_none() is not None


async def _get_or_create_contact(
    db: AsyncSession, *, tenant_id: uuid.UUID, sender_address: str, sender_name: str | None
) -> Contact:
    result = await db.execute(
        select(Contact).where(
            Contact.tenant_id == tenant_id, Contact.email_address == sender_address
        )
    )
    contact = result.scalar_one_or_none()
    if contact is None:
        contact = Contact(
            tenant_id=tenant_id,
            email_address=fit(sender_address, Contact, "email_address"),
            name=fit(sender_name, Contact, "name"),
        )
        db.add(contact)
        await db.flush()
    elif sender_name and not contact.name:
        contact.name = fit(sender_name, Contact, "name")
    return contact


async def _get_or_create_case(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    contact: Contact,
    embedding: list[float],
    suggested_title: str | None,
    subject: str | None,
) -> tuple[Case, bool, float | None]:
    match = await case_matching.find_matching_case(
        db, tenant_id=tenant_id, contact_id=contact.id, embedding=embedding
    )
    if match is not None:
        # Ensure this contact is linked to the case (supports N contacts/case).
        existing = await db.execute(
            select(CaseContact).where(
                CaseContact.case_id == match.case.id, CaseContact.contact_id == contact.id
            )
        )
        if existing.scalar_one_or_none() is None:
            db.add(CaseContact(case_id=match.case.id, contact_id=contact.id))
            await db.flush()
        return match.case, False, match.similarity

    title = suggested_title or subject or f"Korrespondenz mit {contact.email_address}"
    case = Case(tenant_id=tenant_id, title=fit(title, Case, "title"))
    db.add(case)
    await db.flush()
    db.add(CaseContact(case_id=case.id, contact_id=contact.id))
    await db.flush()
    return case, True, None


def _build_email(
    *,
    mailbox: Mailbox,
    fetched: FetchedEmail,
    contact: Contact,
    case: Case,
    result: classification.ClassificationResult,
    embedding: list[float],
) -> EmailMessage:
    """Maps a fetched mail onto its row.

    Header values arrive from the outside world and routinely exceed the
    bounds their columns declare (a multi-kilobyte display name is a
    standard spam pattern), so each bounded field goes through `fit` - see
    app/models/limits.py.
    """
    return EmailMessage(
        tenant_id=mailbox.tenant_id,
        mailbox_id=mailbox.id,
        contact_id=contact.id,
        case_id=case.id,
        gmail_message_id=fit(fetched.gmail_message_id, EmailMessage, "gmail_message_id"),
        gmail_thread_id=fit(fetched.gmail_thread_id, EmailMessage, "gmail_thread_id"),
        rfc822_message_id=fit(fetched.rfc822_message_id, EmailMessage, "rfc822_message_id"),
        subject=fit(fetched.subject, EmailMessage, "subject"),
        sender_address=fit(fetched.sender_address, EmailMessage, "sender_address"),
        sender_name=fit(fetched.sender_name, EmailMessage, "sender_name"),
        raw_content=fetched.raw_content,
        snippet=fit(fetched.snippet, EmailMessage, "snippet"),
        wichtigkeits_kategorie=result.wichtigkeits_kategorie,
        typ=result.typ,
        classification_confidence=result.confidence,
        classification_reasoning=result.reasoning,
        status=_STATUS_BY_WICHTIGKEIT[result.wichtigkeits_kategorie],
        embedding=embedding,
        received_at=fetched.received_at,
        # When *we* handled it, as opposed to when it was sent. Needed to
        # tell "old mail, just imported" from "arrived and sat unprocessed",
        # which the audit trail alone cannot answer.
        processed_at=datetime.now(UTC),
    )


def _attach_metadata(db: AsyncSession, *, email: EmailMessage, fetched: FetchedEmail) -> None:
    """Persists attachment metadata (filename, type, size, Gmail id).

    The payload itself is not fetched; Attachment.storage_path is the
    prepared hook for that (see README "Ausbaustufen").
    """
    for attachment in fetched.attachments:
        db.add(
            Attachment(
                tenant_id=email.tenant_id,
                email_message_id=email.id,
                filename=fit(attachment.filename, Attachment, "filename"),
                content_type=fit(attachment.content_type, Attachment, "content_type"),
                size_bytes=attachment.size_bytes,
                gmail_attachment_id=fit(
                    attachment.gmail_attachment_id, Attachment, "gmail_attachment_id"
                ),
            )
        )


async def _log_classification(
    db: AsyncSession,
    *,
    email: EmailMessage,
    result: classification.ClassificationResult,
    case: Case,
    case_created: bool,
    similarity: float | None,
) -> None:
    await log_action(
        db,
        tenant_id=email.tenant_id,
        actor=ActionActor.SYSTEM,
        entity_type="email_message",
        entity_id=email.id,
        action="classified",
        detail={
            "wichtigkeits_kategorie": result.wichtigkeits_kategorie.value,
            "typ": result.typ.value,
            "confidence": result.confidence,
            "reasoning": result.reasoning,
        },
    )
    await log_action(
        db,
        tenant_id=email.tenant_id,
        actor=ActionActor.SYSTEM,
        entity_type="email_message",
        entity_id=email.id,
        action="case_created" if case_created else "case_matched",
        detail={"case_id": str(case.id), "similarity": similarity},
    )


async def _generate_draft_for(db: AsyncSession, *, email: EmailMessage) -> Draft | None:
    """Generates and stores a reply draft for an antwort_erforderlich mail.

    Returns None if the model produced no usable body - storing a blank
    draft would just put an empty editor in front of a human with no
    indication of why.
    """
    subject, body, rag_summary = await generate_draft(db, email=email)
    if not body.strip():
        logger.warning("draft_generation_empty_body email=%s", email.id)
        return None

    draft = Draft(
        tenant_id=email.tenant_id,
        email_message_id=email.id,
        subject=fit(subject, Draft, "subject"),
        body=body,
        rag_context_summary=rag_summary,
    )
    db.add(draft)
    await db.flush()
    await log_action(
        db,
        tenant_id=email.tenant_id,
        actor=ActionActor.SYSTEM,
        entity_type="draft",
        entity_id=draft.id,
        action="draft_generated",
        detail={"email_message_id": str(email.id)},
    )
    return draft


async def _run_filing_action(
    db: AsyncSession, *, email: EmailMessage, contact: Contact, case: Case
) -> None:
    """Records what was done with a mail that needs no reply."""
    action = _FILING_ACTION_BY_WICHTIGKEIT.get(email.wichtigkeits_kategorie)
    if action is None:
        return
    await log_action(
        db,
        tenant_id=email.tenant_id,
        actor=ActionActor.SYSTEM,
        entity_type="email_message",
        entity_id=email.id,
        action=action,
        detail={"contact_id": str(contact.id), "case_id": str(case.id)},
    )


async def _flag_product_suggestion(
    db: AsyncSession, *, email: EmailMessage, result: classification.ClassificationResult
) -> None:
    """Files a pending ProductSuggestion when classify_email detected a new
    article number + description in this mail - see
    app/services/product_suggestion_service.py. Piggybacks on the
    classification call already made above; never calls the model itself.

    Independent of wichtigkeits_kategorie: a supplier announcing a new
    article is routine "information" mail, not a reply-needed one, but the
    detection should not be tied to any one category.
    """
    suggestion = await maybe_create_suggestion(
        db, tenant_id=email.tenant_id, email=email, result=result
    )
    if suggestion is None:
        return
    await log_action(
        db,
        tenant_id=email.tenant_id,
        actor=ActionActor.SYSTEM,
        entity_type="product_suggestion",
        entity_id=suggestion.id,
        action="product_suggestion_created",
        detail={"sku": suggestion.sku, "email_message_id": str(email.id)},
    )


async def process_incoming_email(
    db: AsyncSession,
    *,
    mailbox: Mailbox,
    fetched: FetchedEmail,
) -> EmailMessage | None:
    """Processes one fetched mail end to end.

    Idempotent: returns None (and does nothing) if this Gmail message was
    already processed for this mailbox. Owns the transaction - the caller
    (app/workers/tasks.py) relies on a committed row to know it need not
    re-spend Claude tokens on this mail.
    """
    if await _already_processed(
        db, mailbox=mailbox, gmail_message_id=fetched.gmail_message_id
    ):
        return None

    tenant_id = mailbox.tenant_id

    contact = await _get_or_create_contact(
        db,
        tenant_id=tenant_id,
        sender_address=fetched.sender_address,
        sender_name=fetched.sender_name,
    )

    result = await classification.classify_email(
        subject=fetched.subject,
        sender_address=fetched.sender_address,
        body=fetched.raw_content,
        list_unsubscribe=fetched.list_unsubscribe,
    )

    embedding = await embeddings.embed_text(f"{fetched.subject or ''}\n\n{fetched.raw_content}")

    case, case_created, similarity = await _get_or_create_case(
        db,
        tenant_id=tenant_id,
        contact=contact,
        embedding=embedding,
        suggested_title=result.suggested_case_title,
        subject=fetched.subject,
    )

    email = _build_email(
        mailbox=mailbox,
        fetched=fetched,
        contact=contact,
        case=case,
        result=result,
        embedding=embedding,
    )
    db.add(email)
    await db.flush()

    _attach_metadata(db, email=email, fetched=fetched)
    await _log_classification(
        db,
        email=email,
        result=result,
        case=case,
        case_created=case_created,
        similarity=similarity,
    )

    if result.wichtigkeits_kategorie is WichtigkeitsKategorie.ANTWORT_ERFORDERLICH:
        await _generate_draft_for(db, email=email)
    else:
        await _run_filing_action(db, email=email, contact=contact, case=case)

    await _flag_product_suggestion(db, email=email, result=result)

    await db.commit()
    return email
