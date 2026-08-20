from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import TenantScopedMixin, TimestampMixin, UUIDPKMixin


class User(UUIDPKMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    tenant = relationship("Tenant", back_populates="users")
    mailboxes = relationship("Mailbox", back_populates="user", cascade="all, delete-orphan")
