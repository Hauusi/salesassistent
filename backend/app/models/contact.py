from __future__ import annotations

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import TenantScopedMixin, TimestampMixin, UUIDPKMixin


class Contact(UUIDPKMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "contacts"
    __table_args__ = (UniqueConstraint("tenant_id", "email_address", name="uq_contact_tenant_email"),)

    email_address: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)

    emails = relationship("EmailMessage", back_populates="contact")
    cases = relationship("CaseContact", back_populates="contact", cascade="all, delete-orphan")
