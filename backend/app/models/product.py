from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TenantScopedMixin, TimestampMixin, UUIDPKMixin


class Product(UUIDPKMixin, TenantScopedMixin, TimestampMixin, Base):
    """A product/service in the tenant's catalog, used as grounding context
    for draft generation on Angebotsanfragen (typ=anfrage) - see
    app/services/product_search.py and app/services/draft_generation.py."""

    __tablename__ = "products"
    __table_args__ = (
        # NULL SKUs don't participate in the uniqueness check (standard SQL
        # NULL semantics), so products without an SKU are unaffected.
        UniqueConstraint("tenant_id", "sku", name="uq_product_tenant_sku"),
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
