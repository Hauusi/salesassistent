from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import TenantScopedMixin, TimestampMixin, UUIDPKMixin
from app.models.enums import MailboxPollStatus, MailboxProvider


class Mailbox(UUIDPKMixin, TenantScopedMixin, TimestampMixin, Base):
    """A connected mailbox. OAuth tokens are stored encrypted at rest
    (see app.services.crypto) - never in plaintext."""

    __tablename__ = "mailboxes"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[MailboxProvider] = mapped_column(
        Enum(MailboxProvider, native_enum=False), nullable=False, default=MailboxProvider.GMAIL
    )
    email_address: Mapped[str] = mapped_column(String(320), nullable=False, index=True)

    # Encrypted (Fernet) OAuth token material. Never store plaintext.
    access_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    granted_scopes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Polling bookmark: Gmail historyId of the last message we processed,
    # so the next poll only fetches what's new. Also the hook point for a
    # future Gmail Pub/Sub push upgrade (out of scope for this MVP).
    last_history_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Outcome of the most recent poll *attempt*, updated by
    # app/workers/tasks.py on every run - success or crash - so a failure
    # is visible here instead of only in container logs. Null means "never
    # polled yet", not "ok".
    last_poll_status: Mapped[MailboxPollStatus | None] = mapped_column(
        Enum(MailboxPollStatus, native_enum=False), nullable=True
    )
    last_poll_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    user = relationship("User", back_populates="mailboxes")
    emails = relationship("EmailMessage", back_populates="mailbox", cascade="all, delete-orphan")
