"""Product lookup for grounding draft generation on Angebotsanfragen
(typ=anfrage) - see app/services/draft_generation.py.

Backed by Postgres full-text search rather than hand-rolled keyword
matching. The previous implementation carried two workarounds that the
database does properly:

- Stemming by chopping the last character off words longer than five
  ("Aluminiumprofile" -> "Aluminiumprofil"). That only ever worked for the
  "-e" plural: "Motoren" became "motore" and never matched "Motor", and
  anything five characters or shorter was not stemmed at all.
- An 80-entry hardcoded German stopword list. Not tenant-aware, not
  translatable, and it dropped tokens that carry meaning in a product
  context ("zwei" is a stopword, so a "Zwei-Komponenten-Kleber" category
  could not be found by that word).

`to_tsvector`/`to_tsquery` with a language configuration handle both,
correctly and in every language Postgres ships a configuration for. The
configuration is set via PRODUCT_SEARCH_TEXT_CONFIG and validated at
startup (see app/services/startup_checks.py).

Terms are OR-ed rather than AND-ed: an inquiry mentions far more words
than any single catalog entry contains, so requiring all of them would
match nothing. `ts_rank` then orders by how well each product covers the
inquiry, which is what the old "count how many keywords hit" score was
approximating.
"""
from __future__ import annotations

import uuid

from sqlalchemy import Text, cast, column, func, literal, literal_column, or_, select
from sqlalchemy.dialects.postgresql import REGCONFIG, TSQUERY
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.product import Product


def _text_config():
    """The Postgres text-search configuration to stem and stop-word with."""
    return cast(literal(get_settings().product_search_text_config), REGCONFIG)


def _searchable_document():
    """The concatenated product text that a query is matched against.

    Weighted so a hit in the name outranks one in the description: a
    product *called* "Aluminiumprofil" is a better answer to a request for
    aluminium profiles than one that merely mentions them in prose.
    """
    cfg = _text_config()

    def weighted(field, weight: str):
        # setweight's second argument is Postgres' "char" type, which a
        # bound varchar parameter does not coerce to - hence the literal.
        return func.setweight(
            func.to_tsvector(cfg, func.coalesce(field, "")), literal_column(f"'{weight}'")
        )

    return (
        weighted(Product.name, "A")
        .op("||")(weighted(Product.category, "B"))
        .op("||")(weighted(Product.sku, "B"))
        .op("||")(weighted(Product.description, "C"))
    )


def _or_tsquery(query_text: str):
    """Turns free text into an OR-ed tsquery, via the configuration's own
    stemmer and stopword list.

    Runs the text through to_tsvector first (which stems and drops
    stopwords), then re-joins the surviving lexemes with "|". Yields NULL
    when nothing survives - a greetings-only mail, say - and `@@ NULL` is
    never true, so that needs no special case in the caller.
    """
    lexemes = (
        select(func.string_agg(column("lexeme"), literal(" | ")))
        .select_from(
            func.unnest(
                func.to_tsvector(_text_config(), cast(literal(query_text), Text))
            ).alias("lex")
        )
        .scalar_subquery()
    )
    return cast(func.nullif(lexemes, ""), TSQUERY)


async def search_products(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    query_text: str,
    query_embedding: list[float] | None = None,
    limit: int = 5,
) -> list[Product]:
    """Finds catalog entries relevant to `query_text`, best match first.

    Combines two independent signals:

    - Keyword match (Postgres full-text search, stemmed) - the original
      behaviour, high precision, finds exact/stemmed vocabulary overlap.
    - Vector similarity (pgvector cosine distance), when `query_embedding`
      is supplied - finds semantically related products that share no
      keyword with the inquiry at all (e.g. "LED Leuchtbalken" finding a
      catalog entry named "Lichtleiste LED"). This module never calls the
      embedding model itself: the caller passes in whatever embedding its
      own text already has (see app/services/draft_generation.py, which
      reuses the inquiry mail's embedding rather than paying for a second
      one at search time - see app/services/product_embedding.py for the
      matching rule on the catalog side).

    Keyword hits are listed first (ranked by ts_rank) - an exact
    vocabulary match is the stronger, more directly explainable signal.
    Vector-only hits (products the keyword search did not already find)
    fill the remaining `limit` slots, ranked by similarity and gated by
    PRODUCT_SEARCH_SIMILARITY_THRESHOLD so a merely-adjacent product is
    never presented as if it answered the inquiry.

    Returns an empty list when nothing matches either way - callers should
    treat that as "no grounding available", not an error.
    """
    if not query_text or not query_text.strip():
        return []

    document = _searchable_document()
    query = _or_tsquery(query_text)

    keyword_stmt = (
        select(Product)
        .where(Product.tenant_id == tenant_id, document.op("@@")(query))
        .order_by(func.ts_rank(document, query).desc(), Product.name)
        .limit(limit)
    )
    keyword_matches = list((await db.execute(keyword_stmt)).scalars().all())

    remaining = limit - len(keyword_matches)
    if query_embedding is None or remaining <= 0:
        return keyword_matches

    distance_expr = Product.embedding.cosine_distance(query_embedding)
    max_distance = 1.0 - get_settings().product_search_similarity_threshold
    vector_stmt = (
        select(Product)
        .where(
            Product.tenant_id == tenant_id,
            Product.embedding.isnot(None),
            distance_expr <= max_distance,
        )
        .order_by(distance_expr)
        .limit(remaining)
    )
    already_found = [p.id for p in keyword_matches]
    if already_found:
        vector_stmt = vector_stmt.where(Product.id.notin_(already_found))

    vector_matches = list((await db.execute(vector_stmt)).scalars().all())
    return keyword_matches + vector_matches


async def search_products_by_text(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    q: str | None = None,
    category: str | None = None,
    limit: int = 200,
) -> list[Product]:
    """Catalog browsing for the UI: substring matching, not relevance
    ranking.

    Deliberately not the same function as `search_products` above. A person
    typing into a filter box expects "show me rows containing what I typed",
    including partial words and SKU fragments; an inquiry mail needs
    stemmed, ranked relevance. Conflating the two makes one of them wrong.
    """
    stmt = select(Product).where(Product.tenant_id == tenant_id).order_by(Product.name).limit(limit)

    if category:
        stmt = stmt.where(Product.category == category)
    if q and q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Product.name.ilike(like),
                Product.description.ilike(like),
                Product.category.ilike(like),
                Product.sku.ilike(like),
            )
        )

    result = await db.execute(stmt)
    return list(result.scalars().all())


# `Product.description` (free Text) and `Product.specs` (free-form JSONB,
# e.g. from CSV import - see app/services/product_import.py) have no length
# limit at the DB/schema level. Left uncapped here, a handful of verbose
# catalog entries (long marketing copy, a specs dict with dozens of keys)
# would silently dominate the draft-generation prompt - capped instead so
# the model still gets the concrete values it needs to quote (price,
# availability, key specs) without paying for the full text of every match.
_MAX_DESCRIPTION_CHARS = 220
_MAX_SPECS_ENTRIES = 6
_MAX_SPECS_CHARS = 200


def _truncate(text: str, max_chars: int) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "…"


def format_products_for_prompt(products: list[Product]) -> str:
    if not products:
        return ""
    lines = []
    for p in products:
        parts = [p.name]
        if p.category:
            parts.append(f"Kategorie: {p.category}")
        if p.sku:
            parts.append(f"SKU: {p.sku}")
        if p.price is not None:
            parts.append(f"Preis: {p.price} {p.currency}")
        if p.availability:
            parts.append(f"Verfügbarkeit: {p.availability}")
        if p.specs:
            # Most specific/short fields first tend to be the most useful
            # to quote back to the customer; a dict with many entries is
            # capped by count, and the rendered string capped by length as
            # a second safety net against a single oversized value.
            specs_str = ", ".join(f"{k}: {v}" for k, v in list(p.specs.items())[:_MAX_SPECS_ENTRIES])
            parts.append(f"Specs: {_truncate(specs_str, _MAX_SPECS_CHARS)}")
        line = " | ".join(parts)
        if p.description:
            line += f"\n  Beschreibung: {_truncate(p.description, _MAX_DESCRIPTION_CHARS)}"
        lines.append(f"- {line}")
    return "\n".join(lines)
