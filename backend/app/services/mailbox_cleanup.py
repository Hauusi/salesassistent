"""Deletes a mailbox and everything that only existed because of it.

email_messages/drafts/attachments/product_suggestions already cascade at
the database level - see each model's `ForeignKey(..., ondelete="CASCADE")`
(email_messages.mailbox_id, drafts/attachments/product_suggestions.email_
message_id). Deleting the Mailbox row removes all of them in one
statement; nothing here needs to touch those tables directly.

Contact and Case cannot cascade the same way: neither has a mailbox_id -
a Contact/Case can span several mailboxes of the same tenant (e.g. one
customer emailing two different connected accounts), so the FK direction
runs the other way (email_messages.contact_id/case_id, both
ON DELETE SET NULL - deleting a Contact/Case must not delete its mail
history). This module is the cleanup a plain CASCADE cannot express:
after the mailbox's mail is gone, delete only the Contact/Case rows now
referenced by zero remaining email anywhere in the tenant - not every
Contact/Case the deleted mailbox's mail ever touched, since some of those
may still have mail from a different mailbox of the same tenant.
"""
from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.case import Case
from app.models.contact import Contact
from app.models.email_message import EmailMessage
from app.models.mailbox import Mailbox


async def delete_mailbox_and_orphans(db: AsyncSession, *, mailbox: Mailbox) -> None:
    """Deletes `mailbox` and any contact/case left orphaned by it.

    Does not commit - the caller owns the transaction boundary, same as
    every other write in this codebase's route handlers.
    """
    tenant_id = mailbox.tenant_id

    # Candidates: every contact/case this mailbox's mail ever touched.
    # Collected *before* the delete - the rows that would tell us this are
    # exactly the ones about to disappear.
    candidate_contact_ids = set(
        (
            await db.execute(
                select(EmailMessage.contact_id).where(
                    EmailMessage.mailbox_id == mailbox.id, EmailMessage.contact_id.isnot(None)
                )
            )
        ).scalars()
    )
    candidate_case_ids = set(
        (
            await db.execute(
                select(EmailMessage.case_id).where(
                    EmailMessage.mailbox_id == mailbox.id, EmailMessage.case_id.isnot(None)
                )
            )
        ).scalars()
    )

    # Cascades away email_messages, and from there drafts, attachments and
    # product_suggestions - see the module docstring.
    await db.delete(mailbox)
    await db.flush()

    if candidate_contact_ids:
        still_referenced = set(
            (
                await db.execute(
                    select(EmailMessage.contact_id).where(
                        EmailMessage.contact_id.in_(candidate_contact_ids)
                    )
                )
            ).scalars()
        )
        orphaned = candidate_contact_ids - still_referenced
        if orphaned:
            # case_contacts.contact_id is ON DELETE CASCADE, so this also
            # removes this contact's now-meaningless case memberships.
            await db.execute(
                delete(Contact).where(Contact.id.in_(orphaned), Contact.tenant_id == tenant_id)
            )

    if candidate_case_ids:
        still_referenced = set(
            (
                await db.execute(
                    select(EmailMessage.case_id).where(EmailMessage.case_id.in_(candidate_case_ids))
                )
            ).scalars()
        )
        orphaned = candidate_case_ids - still_referenced
        if orphaned:
            await db.execute(
                delete(Case).where(Case.id.in_(orphaned), Case.tenant_id == tenant_id)
            )
