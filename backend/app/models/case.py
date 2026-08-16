from __future__ import annotations

import uuid

from sqlalchemy import Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import TenantScopedMixin, TimestampMixin, UUIDPKMixin
from app.models.enums import CaseStatus


class Case(UUIDPKMixin, TenantScopedMixin, TimestampMixin, Base):
    """A topic/thread grouping correspondence across possibly several
    contacts (e.g. multiple people at the same customer discussing one
    order or project)."""

    __tablename__ = "cases"

    title: Mapped[str] = mapped_column(String(500), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[CaseStatus] = mapped_column(
        Enum(CaseStatus, native_enum=False), nullable=False, default=CaseStatus.OFFEN
    )

    contacts = relationship("CaseContact", back_populates="case", cascade="all, delete-orphan")
    emails = relationship("EmailMessage", back_populates="case")


class CaseContact(UUIDPKMixin, Base):
    """N:M association between Case and Contact."""

    __tablename__ = "case_contacts"

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True
    )

    case = relationship("Case", back_populates="contacts")
    contact = relationship("Contact", back_populates="cases")
