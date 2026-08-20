"""add GIN index for product full-text search

Backs app/services/product_search.py, which matches inquiry text against
the catalog with to_tsvector/to_tsquery instead of the ILIKE keyword
matching it used before.

The index is expression-based and must mirror the searchable document that
module builds, weights included - Postgres only uses it when the query's
expression is identical. It is also tied to one text-search configuration,
so it is built for PRODUCT_SEARCH_TEXT_CONFIG's default ('german'). A
deployment that changes that setting should add an index for its own
configuration; the search stays correct either way, it just falls back to
a sequential scan.

Revision ID: a1b2c3d4e5f6
Revises: 89010603d993
"""
from typing import Sequence, Union

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "89010603d993"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DOCUMENT = (
    "setweight(to_tsvector('german', coalesce(name, '')), 'A') || "
    "setweight(to_tsvector('german', coalesce(category, '')), 'B') || "
    "setweight(to_tsvector('german', coalesce(sku, '')), 'B') || "
    "setweight(to_tsvector('german', coalesce(description, '')), 'C')"
)


def upgrade() -> None:
    op.execute(
        f"CREATE INDEX ix_products_search_document ON products USING gin (({_DOCUMENT}))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_products_search_document")
