from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import TenantScopedMixin, TimestampMixin, UUIDPKMixin
from app.models.enums import DraftStatus


class Draft(UUIDPKMixin, TenantScopedMixin, TimestampMixin, Base):
    """An LLM-generated reply proposal for an EmailMessage. Never sent
    automatically - only via the explicit approval workflow."""

    __tablename__ = "drafts"

    email_message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("email_messages.id", ondelete="CASCADE"), nullable=False, index=True
    )

    subject: Mapped[str | None] = mapped_column(String(998), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[DraftStatus] = mapped_column(
        Enum(DraftStatus, native_enum=False), nullable=False, default=DraftStatus.ENTWURF, index=True
    )

    # RAG context that was fed into generation, kept for traceability/debugging.
    rag_context_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    gmail_sent_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    email_message = relationship("EmailMessage", back_populates="drafts")
