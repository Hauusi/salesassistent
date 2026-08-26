from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import TenantScopedMixin, TimestampMixin, UUIDPKMixin
from app.models.enums import ProductSuggestionStatus


class ProductSuggestion(UUIDPKMixin, TenantScopedMixin, TimestampMixin, Base):
    """A candidate catalog entry detected in an incoming mail (an SKU plus
    its own article text), pending human review - see
    app/services/product_suggestion_service.py for how it is created and
    app/api/routes/product_suggestions.py for the approve/reject workflow.

    Never becomes a Product on its own. Approval is the one point where a
    human, not the model, decides what enters the tenant's real catalog -
    the same shape as the Draft approval gate in app/models/draft.py."""

    __tablename__ = "product_suggestions"

    email_message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("email_messages.id", ondelete="CASCADE"), nullable=False, index=True
    )

    sku: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    # A short, human-readable title distinct from the full article text -
    # Product.name is required, and the mail's own wording rarely doubles
    # as a good catalog title on its own (see app/models/product.py).
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[ProductSuggestionStatus] = mapped_column(
        Enum(ProductSuggestionStatus, native_enum=False),
        nullable=False,
        default=ProductSuggestionStatus.VORGESCHLAGEN,
        index=True,
    )

    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Set once approve_suggestion creates the real catalog row, so the
    # suggestion keeps a durable link to what it became.
    created_product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )

    email_message = relationship("EmailMessage")
