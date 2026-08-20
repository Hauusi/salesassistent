"""End-to-end processing of one fetched mail: contact/case assignment,
classification, and the per-category action logic from concept doc 5.3.

    newsletter          -> label only, no further action
    antwort_erforderlich -> generate draft (RAG), status = wartet_auf_freigabe
    information          -> file structured, searchable, status = abgelegt
    bestellung / anfrage -> additionally flagged via `typ` (orthogonal)
    spam_verdacht        -> flagged, hidden from default view, never deleted

Every step that makes an automated decision is written to ActionLog.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.attachment import Attachment
from app.models.case import Case, CaseContact
from app.models.contact import Contact
from app.models.email_message import EmailMessage
from app.models.enums import ActionActor, EmailStatus, WichtigkeitsKategorie
from app.models.limits import fit
from app.models.mailbox import Mailbox
from app.services import case_matching, classification, embeddings
from app.services.action_log_service import log_action
from app.services.draft_generation import generate_draft
from app.services.gmail_client import FetchedEmail
from app.models.draft import Draft

logger = logging.getLogger(__name__)

_STATUS_BY_WICHTIGKEIT = {
    WichtigkeitsKategorie.NEWSLETTER: EmailStatus.ERLEDIGT,
    WichtigkeitsKategorie.ANTWORT_ERFORDERLICH: EmailStatus.WARTET_AUF_FREIGABE,
    WichtigkeitsKategorie.INFORMATION: EmailStatus.ABGELEGT,
    WichtigkeitsKategorie.SPAM_VERDACHT: EmailStatus.AUSGEBLENDET,
}


async def _get_or_create_contact(db: AsyncSession, *, tenant_id, sender_address: str, sender_name: str | None) -> Contact:
    result = await db.execute(
        select(Contact).where(Contact.tenant_id == tenant_id, Contact.email_address == sender_address)
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
    db: AsyncSession, *, tenant_id, contact: Contact, embedding: list[float], suggested_title: str | None, subject: str | None
) -> tuple[Case, bool, float | None]:
    match = await case_matching.find_matching_case(db, tenant_id=tenant_id, contact_id=contact.id, embedding=embedding)
    if match is not None:
        # Ensure this contact is linked to the case (supports N contacts/case).
        existing = await db.execute(
            select(CaseContact).where(CaseContact.case_id == match.case.id, CaseContact.contact_id == contact.id)
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


def _attach_metadata(db: AsyncSession, *, email: EmailMessage, fetched: FetchedEmail) -> None:
    """Persists attachment metadata (filename, type, size, Gmail id).

    The parser already collected this and the Attachment table already
    existed, but nothing ever wrote a row - so the table was empty while
    the README claimed the metadata was captured. The payload itself still
    is not fetched; Attachment.storage_path is the prepared hook for that
    (see README "Ausbaustufen").
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


async def process_incoming_email(
    db: AsyncSession,
    *,
    mailbox: Mailbox,
    fetched: FetchedEmail,
) -> EmailMessage | None:
    """Idempotent: returns None (and does nothing) if this Gmail message was
    already processed for this mailbox."""

    existing = await db.execute(
        select(EmailMessage).where(
            EmailMessage.mailbox_id == mailbox.id,
            EmailMessage.gmail_message_id == fetched.gmail_message_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        return None

    tenant_id = mailbox.tenant_id

    contact = await _get_or_create_contact(
        db, tenant_id=tenant_id, sender_address=fetched.sender_address, sender_name=fetched.sender_name
    )

    classification_result = await classification.classify_email(
        subject=fetched.subject,
        sender_address=fetched.sender_address,
        body=fetched.raw_content,
        list_unsubscribe=fetched.list_unsubscribe,
    )

    embedding_text = f"{fetched.subject or ''}\n\n{fetched.raw_content}"
    embedding = await embeddings.embed_text(embedding_text)

    case, case_created, similarity = await _get_or_create_case(
        db,
        tenant_id=tenant_id,
        contact=contact,
        embedding=embedding,
        suggested_title=classification_result.suggested_case_title,
        subject=fetched.subject,
    )

    status = _STATUS_BY_WICHTIGKEIT[classification_result.wichtigkeits_kategorie]

    email = EmailMessage(
        tenant_id=tenant_id,
        mailbox_id=mailbox.id,
        contact_id=contact.id,
        case_id=case.id,
        # Header values arrive from the outside world and routinely exceed
        # the bounds their columns declare (a multi-kilobyte display name is
        # a standard spam pattern). Unfitted, Postgres raises
        # StringDataRightTruncation and aborts the batch - see
        # app/models/limits.py.
        gmail_message_id=fit(fetched.gmail_message_id, EmailMessage, "gmail_message_id"),
        gmail_thread_id=fit(fetched.gmail_thread_id, EmailMessage, "gmail_thread_id"),
        rfc822_message_id=fit(fetched.rfc822_message_id, EmailMessage, "rfc822_message_id"),
        subject=fit(fetched.subject, EmailMessage, "subject"),
        sender_address=fit(fetched.sender_address, EmailMessage, "sender_address"),
        sender_name=fit(fetched.sender_name, EmailMessage, "sender_name"),
        raw_content=fetched.raw_content,
        snippet=fit(fetched.snippet, EmailMessage, "snippet"),
        wichtigkeits_kategorie=classification_result.wichtigkeits_kategorie,
        typ=classification_result.typ,
        classification_confidence=classification_result.confidence,
        classification_reasoning=classification_result.reasoning,
        status=status,
        embedding=embedding,
        received_at=fetched.received_at,
    )
    db.add(email)
    await db.flush()

    _attach_metadata(db, email=email, fetched=fetched)

    await log_action(
        db,
        tenant_id=tenant_id,
        actor=ActionActor.SYSTEM,
        entity_type="email_message",
        entity_id=email.id,
        action="classified",
        detail={
            "wichtigkeits_kategorie": classification_result.wichtigkeits_kategorie.value,
            "typ": classification_result.typ.value,
            "confidence": classification_result.confidence,
            "reasoning": classification_result.reasoning,
        },
    )
    await log_action(
        db,
        tenant_id=tenant_id,
        actor=ActionActor.SYSTEM,
        entity_type="email_message",
        entity_id=email.id,
        action="case_created" if case_created else "case_matched",
        detail={"case_id": str(case.id), "similarity": similarity},
    )

    if classification_result.wichtigkeits_kategorie == WichtigkeitsKategorie.ANTWORT_ERFORDERLICH:
        subject, body, rag_summary = await generate_draft(db, email=email)
        draft = Draft(
            tenant_id=tenant_id,
            email_message_id=email.id,
            subject=subject,
            body=body,
            rag_context_summary=rag_summary,
        )
        db.add(draft)
        await db.flush()
        await log_action(
            db,
            tenant_id=tenant_id,
            actor=ActionActor.SYSTEM,
            entity_type="draft",
            entity_id=draft.id,
            action="draft_generated",
            detail={"email_message_id": str(email.id)},
        )
    elif classification_result.wichtigkeits_kategorie == WichtigkeitsKategorie.SPAM_VERDACHT:
        await log_action(
            db,
            tenant_id=tenant_id,
            actor=ActionActor.SYSTEM,
            entity_type="email_message",
            entity_id=email.id,
            action="hidden_as_spam_verdacht",
            detail={},
        )
    elif classification_result.wichtigkeits_kategorie == WichtigkeitsKategorie.INFORMATION:
        await log_action(
            db,
            tenant_id=tenant_id,
            actor=ActionActor.SYSTEM,
            entity_type="email_message",
            entity_id=email.id,
            action="filed_as_information",
            detail={"contact_id": str(contact.id), "case_id": str(case.id)},
        )
    elif classification_result.wichtigkeits_kategorie == WichtigkeitsKategorie.NEWSLETTER:
        await log_action(
            db,
            tenant_id=tenant_id,
            actor=ActionActor.SYSTEM,
            entity_type="email_message",
            entity_id=email.id,
            action="labeled_newsletter",
            detail={},
        )

    await db.commit()
    return email
