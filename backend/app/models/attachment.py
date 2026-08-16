from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import TenantScopedMixin, TimestampMixin, UUIDPKMixin


class Attachment(UUIDPKMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "attachments"

    email_message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("email_messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gmail_attachment_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Object-storage pointer for the fetched payload. MVP does not
    # implement a storage backend yet - metadata is captured so a storage
    # integration can be dropped in without a schema change.
    storage_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    email_message = relationship("EmailMessage", back_populates="attachments")
