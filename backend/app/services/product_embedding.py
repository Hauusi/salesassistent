"""Computes and stores a Product's embedding, for semantic search - see
app/services/product_search.py.

Deliberately the only place a product's embedding gets computed: once, on
create or when an embedded field (name/category/description) changes -
never at search time. A search reuses whatever embedding its own query
text already has (the inquiry mail's, at the one call site that matters -
see app/services/draft_generation.py) instead of spending a Voyage call
per search.
"""
from __future__ import annotations

from app.models.product import Product
from app.services import embeddings

# The fields a product's embedding is derived from - also the set that,
# on update, decides whether a recompute is worth its Voyage call (see
# fields_affect_embedding below).
EMBEDDED_FIELDS = frozenset({"name", "category", "description"})


def _embedding_text(*, name: str, category: str | None, description: str | None) -> str:
    return "\n".join(part for part in (name, category, description) if part)


def fields_affect_embedding(changed_fields: set[str]) -> bool:
    """Whether a partial update touched any field the embedding is derived
    from - price/currency/availability/sku/specs changing alone is not
    worth another Voyage call."""
    return bool(changed_fields & EMBEDDED_FIELDS)


async def set_product_embedding(product: Product) -> None:
    """Computes and assigns `product.embedding` from its current
    name/category/description. Does not flush/commit - the caller owns
    the transaction boundary, same as every other write in this codebase."""
    text = _embedding_text(
        name=product.name, category=product.category, description=product.description
    )
    product.embedding = await embeddings.embed_text(text)
