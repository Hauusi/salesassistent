from __future__ import annotations

from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.config import get_settings
from app.db import Base
from app.models.base import TenantScopedMixin, TimestampMixin, UUIDPKMixin

# Read at import time, like the equivalent in app/models/email_message.py -
# a Vector column's width is part of the class definition and cannot be
# deferred. Kept in sync with the migration's fixed width by
# app/services/startup_checks.py.
settings = get_settings()


class Product(UUIDPKMixin, TenantScopedMixin, TimestampMixin, Base):
    """A product/service in the tenant's catalog, used as grounding context
    for draft generation on Angebotsanfragen (typ=anfrage) - see
    app/services/product_search.py and app/services/draft_generation.py."""

    __tablename__ = "products"
    __table_args__ = (
        # NULL SKUs don't participate in the uniqueness check (standard SQL
        # NULL semantics), so products without an SKU are unaffected.
        UniqueConstraint("tenant_id", "sku", name="uq_product_tenant_sku"),
        Index(
            "ix_products_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    sku: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)

    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")

    # Free text on purpose ("3-5 Werktage", "Auf Lager", "Auf Anfrage") -
    # lead times are rarely a clean structured value in practice.
    availability: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Free-form technical specs, e.g. {"Gewicht": "2.4 kg", "Farbe": "silber"}.
    specs: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Embedding of name+category+description, for semantic search (see
    # app/services/product_search.py) - "LED Leuchtbalken" finding a
    # catalog entry named "Lichtleiste LED", which keyword matching alone
    # cannot. Computed once, on create/update (see
    # app/services/product_embedding.py) - never at search time, which
    # reuses the inquiry mail's own already-computed embedding instead.
    # Null until that first computation, or for a catalog imported before
    # this column existed.
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(settings.embedding_dimensions), nullable=True
    )
