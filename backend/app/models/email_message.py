from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import get_settings
from app.db import Base
from app.models.base import TenantScopedMixin, TimestampMixin, UUIDPKMixin
from app.models.enums import EmailStatus, TypKategorie, WichtigkeitsKategorie

# The second (and last) import-time settings read: a Vector column's width
# is part of the class definition and cannot be deferred. Because the
# migration writes a fixed width, the two can drift - which is what
# app/services/startup_checks.py verifies at boot.
settings = get_settings()


class EmailMessage(UUIDPKMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "email_messages"
    __table_args__ = (
        UniqueConstraint("mailbox_id", "gmail_message_id", name="uq_email_mailbox_gmail_id"),
        Index(
            "ix_email_messages_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    mailbox_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mailboxes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Gmail identifiers, needed to thread replies and avoid re-processing.
    gmail_message_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    gmail_thread_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # RFC822 Message-ID header (distinct from Gmail's internal id above) -
    # needed for correct In-Reply-To/References headers when sending a reply.
    rfc822_message_id: Mapped[str | None] = mapped_column(String(998), nullable=True)

    subject: Mapped[str | None] = mapped_column(String(998), nullable=True)
    sender_address: Mapped[str] = mapped_column(String(320), nullable=False)
    sender_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    raw_content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    snippet: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # Classification results (Modul-1 scope, section 5.3)
    wichtigkeits_kategorie: Mapped[WichtigkeitsKategorie | None] = mapped_column(
        Enum(WichtigkeitsKategorie, native_enum=False), nullable=True, index=True
    )
    typ: Mapped[TypKategorie] = mapped_column(
        Enum(TypKategorie, native_enum=False), nullable=False, default=TypKategorie.KEINER
    )
    classification_confidence: Mapped[float | None] = mapped_column(nullable=True)
    classification_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[EmailStatus] = mapped_column(
        Enum(EmailStatus, native_enum=False), nullable=False, default=EmailStatus.NEU, index=True
    )

    # Embedding of subject+body, used for semantic case matching (pgvector).
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(settings.embedding_dimensions), nullable=True
    )

    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    mailbox = relationship("Mailbox", back_populates="emails")
    contact = relationship("Contact", back_populates="emails")
    case = relationship("Case", back_populates="emails")
    drafts = relationship("Draft", back_populates="email_message", cascade="all, delete-orphan")
    attachments = relationship("Attachment", back_populates="email_message", cascade="all, delete-orphan")
