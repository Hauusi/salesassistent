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
    limit: int = 5,
) -> list[Product]:
    """Finds catalog entries relevant to `query_text`, best match first.

    Returns an empty list when nothing matches - callers should treat that
    as "no grounding available", not an error.
    """
    if not query_text or not query_text.strip():
        return []

    document = _searchable_document()
    query = _or_tsquery(query_text)

    stmt = (
        select(Product)
        .where(Product.tenant_id == tenant_id, document.op("@@")(query))
        .order_by(func.ts_rank(document, query).desc(), Product.name)
        .limit(limit)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


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
