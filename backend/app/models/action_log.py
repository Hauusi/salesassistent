from __future__ import annotations

import uuid

from sqlalchemy import Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TenantScopedMixin, TimestampMixin, UUIDPKMixin
from app.models.enums import ActionActor


class ActionLog(UUIDPKMixin, TenantScopedMixin, TimestampMixin, Base):
    """Audit trail: every automated decision the assistant makes
    (classification, draft generation, filing) and every human
    approval/rejection decision. Append-only - never updated or deleted."""

    __tablename__ = "action_logs"

    actor: Mapped[ActionActor] = mapped_column(Enum(ActionActor, native_enum=False), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    entity_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
