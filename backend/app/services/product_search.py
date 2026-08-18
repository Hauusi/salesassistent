"""Keyword-based product lookup for grounding draft generation on
Angebotsanfragen (typ=anfrage) - see app/services/draft_generation.py.

Deliberately plain ILIKE/keyword search rather than embeddings: the
concept scope for this feature explicitly asks for "Textsuche im
Namen/Kategorie/Beschreibung, basierend auf den Begriffen aus der
Anfrage" - a small product catalog doesn't need semantic search, and
this keeps the feature independent of the Voyage embedding pipeline.
"""
from __future__ import annotations

import re
import uuid
from functools import reduce
from operator import add

from sqlalchemy import case, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import Product

# Common German (and a few English) filler words that show up in inquiry
# mails but carry no product-matching signal - excluded so they don't
# drown out the actual product terms when extracting keywords.
_STOPWORDS = {
    "aber", "alle", "als", "also", "auch", "auf", "aus", "bei", "bekommen",
    "benötigen", "bestellen", "bitte", "dabei", "damit", "dann", "das",
    "dass", "dem", "den", "der", "des", "die", "dies", "diese", "dieser",
    "dieses", "doch", "durch", "ein", "eine", "einen", "einer", "eines", "euch",
    "für", "fuer", "gerne", "gruß", "grüße", "haben", "hallo", "hat", "hätten",
    "ich", "ihnen", "ihre", "ihrer", "ist", "können", "könnten", "mit",
    "möchte", "möchten", "nach", "nicht", "noch", "nur", "oder", "sehr",
    "sich", "sie", "sind", "sollten", "über", "und", "uns", "unser",
    "unsere", "vielen", "von", "vor", "wann", "was", "wenn", "werden",
    "wir", "wird", "wäre", "würde", "würden", "zum", "zur", "zwei",
    "anfrage", "angebot", "angeboten", "danke", "email", "freundlichen",
    "geehrte", "geehrter", "damen", "herren", "please", "thanks", "hello",
    "the", "and", "for", "with", "this", "that", "you", "your",
}

_WORD_RE = re.compile(r"[A-Za-zÄÖÜäöüß0-9][A-Za-zÄÖÜäöüß0-9\-]{2,}")


def extract_keywords(text: str, *, limit: int = 15) -> list[str]:
    """Pulls distinct, plausibly product-relevant words out of free text,
    preserving first-seen order. Short/stopword tokens are dropped."""
    seen: dict[str, None] = {}
    for match in _WORD_RE.finditer(text.lower()):
        word = match.group(0)
        if word in _STOPWORDS or word.isdigit():
            continue
        seen.setdefault(word, None)
        if len(seen) >= limit:
            break
    return list(seen.keys())


async def search_products(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    query_text: str,
    limit: int = 5,
) -> list[Product]:
    """Finds products whose name/category/description mention keywords
    from `query_text`, best matches (most keyword hits) first. Returns an
    empty list if no keyword matches anything - callers should treat that
    as "no grounding available", not an error."""
    keywords = extract_keywords(query_text)
    if not keywords:
        return []

    per_keyword_matches = []
    for keyword in keywords:
        # Cheap stemming: drop the last character on longer words so a
        # plural in the inquiry ("Aluminiumprofile") still matches a
        # singular catalog entry ("Aluminiumprofil"), and vice versa -
        # good enough for German noun endings without a real stemmer.
        stem = keyword[:-1] if len(keyword) > 5 else keyword
        pattern = f"%{stem}%"
        per_keyword_matches.append(
            or_(
                Product.name.ilike(pattern),
                Product.category.ilike(pattern),
                Product.description.ilike(pattern),
            )
        )

    # Relevance score: how many distinct keywords hit this product.
    score = reduce(add, (case((m, 1), else_=0) for m in per_keyword_matches))

    stmt = (
        select(Product)
        .where(Product.tenant_id == tenant_id, or_(*per_keyword_matches))
        .order_by(score.desc(), Product.name)
        .limit(limit)
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
