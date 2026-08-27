"""Tests for app.services.product_search against a real Postgres database -
the ranking/matching logic lives in a SQL query, so a DB-free unit test
wouldn't exercise the part that matters.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import Product
from app.models.tenant import Tenant
from app.services.product_search import (
    format_products_for_prompt,
    search_products,
    search_products_by_text,
)


@pytest.fixture
async def tenant(db_session: AsyncSession) -> Tenant:
    t = Tenant(name="Test", slug=f"test-{uuid.uuid4().hex[:8]}")
    db_session.add(t)
    await db_session.flush()
    return t


async def _add_product(db_session: AsyncSession, tenant_id, **kwargs) -> Product:
    defaults = {"name": "Produkt", "currency": "EUR", "specs": {}}
    defaults.update(kwargs)
    product = Product(tenant_id=tenant_id, **defaults)
    db_session.add(product)
    await db_session.flush()
    return product


def _basis_vector(dim: int, dimensions: int = 1024) -> list[float]:
    """A unit vector along axis `dim` - two different axes are exactly
    orthogonal (cosine similarity 0), and the same axis is an exact match
    (similarity 1), so semantic-match tests don't depend on a real
    embedding model's actual notion of similarity."""
    vec = [0.0] * dimensions
    vec[dim] = 1.0
    return vec


async def test_search_finds_a_semantic_match_with_no_shared_keyword(
    db_session: AsyncSession, tenant: Tenant
) -> None:
    """"LED Leuchtbalken" finding a catalog entry named "Lichtleiste LED" -
    the motivating case for the vector-similarity fallback. The query text
    here deliberately shares no stem with the target product at all, so a
    hit can only come from query_embedding."""
    target_vector = _basis_vector(0)
    unrelated_vector = _basis_vector(1)

    target = await _add_product(
        db_session, tenant.id, name="Lichtleiste LED", category="Beleuchtung",
        description="Lineares LED-Leuchtmittel", embedding=target_vector,
    )
    await _add_product(
        db_session, tenant.id, name="Hydraulikschlauch HS-20", category="Hydraulik",
        embedding=unrelated_vector,
    )

    results = await search_products(
        db_session,
        tenant_id=tenant.id,
        query_text="Bitte um Rückmeldung bezüglich unserer Bestellung",
        query_embedding=target_vector,
    )

    assert [p.id for p in results] == [target.id]


async def test_search_ignores_a_similarity_below_the_threshold(
    db_session: AsyncSession, tenant: Tenant
) -> None:
    await _add_product(
        db_session, tenant.id, name="Nicht verwandtes Produkt", embedding=_basis_vector(1)
    )

    results = await search_products(
        db_session,
        tenant_id=tenant.id,
        query_text="Völlig andere Anfrage ohne Bezug",
        query_embedding=_basis_vector(0),  # orthogonal - similarity 0.0
    )

    assert results == []


async def test_search_without_a_query_embedding_only_uses_keywords(
    db_session: AsyncSession, tenant: Tenant
) -> None:
    """Backward compatible: a caller that doesn't have (or pass) an
    embedding yet must still get the existing keyword-only behaviour, not
    an error."""
    await _add_product(
        db_session, tenant.id, name="Lichtleiste LED", embedding=_basis_vector(0)
    )

    results = await search_products(
        db_session,
        tenant_id=tenant.id,
        query_text="Bitte um Rückmeldung bezüglich unserer Bestellung",
    )

    assert results == []


async def test_keyword_hits_are_listed_before_semantic_only_hits(
    db_session: AsyncSession, tenant: Tenant
) -> None:
    keyword_hit = await _add_product(
        db_session, tenant.id, name="Aluminiumprofil AP-40", embedding=_basis_vector(1)
    )
    semantic_hit = await _add_product(
        db_session, tenant.id, name="Lichtleiste LED", embedding=_basis_vector(0)
    )

    results = await search_products(
        db_session,
        tenant_id=tenant.id,
        query_text="Anfrage zu Aluminiumprofilen",
        query_embedding=_basis_vector(0),
    )

    assert [p.id for p in results] == [keyword_hit.id, semantic_hit.id]


async def test_search_products_matches_on_name_category_description(
    db_session: AsyncSession, tenant: Tenant
) -> None:
    await _add_product(
        db_session,
        tenant.id,
        name="Aluminiumprofil AP-40",
        category="Profile",
        description="Eloxiertes Aluminiumprofil, 3m Länge",
        price=12.5,
    )
    await _add_product(db_session, tenant.id, name="Winkelverbinder WV-12", category="Verbinder")

    results = await search_products(
        db_session,
        tenant_id=tenant.id,
        query_text="Anfrage: Angebot für 200 Stück Aluminiumprofile, Länge 3m",
    )

    assert len(results) == 1
    assert results[0].name == "Aluminiumprofil AP-40"


async def test_search_products_ranks_more_keyword_hits_first(
    db_session: AsyncSession, tenant: Tenant
) -> None:
    strong_match = await _add_product(
        db_session,
        tenant.id,
        name="Aluminiumprofil AP-40",
        category="Profile",
        description="Stabiles Aluminiumprofil fuer den Innenausbau",
    )
    weak_match = await _add_product(
        db_session, tenant.id, name="Sonstiges Profil", category="Sonstiges"
    )

    results = await search_products(
        db_session,
        tenant_id=tenant.id,
        query_text="Wir suchen ein Aluminiumprofil fuer den Innenausbau, Kategorie Profile",
    )

    assert [p.id for p in results] == [strong_match.id, weak_match.id]


async def test_search_products_is_tenant_scoped(db_session: AsyncSession, tenant: Tenant) -> None:
    other_tenant = Tenant(name="Andere", slug=f"other-{uuid.uuid4().hex[:8]}")
    db_session.add(other_tenant)
    await db_session.flush()
    await _add_product(db_session, other_tenant.id, name="Aluminiumprofil AP-40", category="Profile")

    results = await search_products(db_session, tenant_id=tenant.id, query_text="Aluminiumprofil")

    assert results == []


async def test_search_products_no_keywords_returns_empty(
    db_session: AsyncSession, tenant: Tenant
) -> None:
    await _add_product(db_session, tenant.id, name="Aluminiumprofil AP-40")

    results = await search_products(db_session, tenant_id=tenant.id, query_text="Hallo und Danke!")

    assert results == []


def test_format_products_for_prompt_includes_price_and_specs() -> None:
    product = Product(
        name="Aluminiumprofil AP-40",
        category="Profile",
        sku="AP-40",
        price=12.5,
        currency="EUR",
        availability="3-5 Werktage",
        specs={"Laenge": "3m"},
        description="Eloxiert",
    )

    text = format_products_for_prompt([product])

    assert "Aluminiumprofil AP-40" in text
    assert "12.5 EUR" in text
    assert "3-5 Werktage" in text
    assert "Laenge: 3m" in text
    assert "Eloxiert" in text


def test_format_products_for_prompt_empty_list() -> None:
    assert format_products_for_prompt([]) == ""


def test_format_products_for_prompt_caps_long_description_and_specs() -> None:
    """description/specs are free-form, unbounded fields (Text / JSONB) -
    a verbose catalog entry must not blow up the draft-generation prompt,
    see app.services.product_search._MAX_DESCRIPTION_CHARS /
    _MAX_SPECS_ENTRIES / _MAX_SPECS_CHARS."""
    product = Product(
        name="Aluminiumprofil AP-40",
        description="X" * 1000,
        specs={f"Spec{i}": "Y" * 30 for i in range(20)},
    )

    text = format_products_for_prompt([product])

    # Truncated with a marker, not silently cut off mid-count.
    assert "X" * 1000 not in text
    assert "…" in text
    # Only a bounded number of spec entries show up.
    assert "Spec19" not in text
    assert "Spec0" in text
    # Overall size stays small regardless of how much input data there was.
    assert len(text) < 600


# --- German stemming, previously hand-rolled and half-broken -------------
#
# The old implementation stemmed by chopping the last character off words
# longer than five, and filtered against an 80-entry hardcoded German
# stopword list. These are the cases that failed.


async def test_plural_en_matches_the_singular_catalog_entry(
    db_session: AsyncSession, tenant: Tenant
) -> None:
    """Regression: "Motoren" was stemmed to "motore", which never matched
    "Motor" - the most common German plural, silently unfindable."""
    await _add_product(db_session, tenant.id, name="Motor MX-200", category="Antriebe")

    found = await search_products(
        db_session, tenant_id=tenant.id, query_text="Wir benötigen zwei Motoren für die Anlage"
    )

    assert [p.name for p in found] == ["Motor MX-200"]


async def test_plural_e_still_matches_the_singular_catalog_entry(
    db_session: AsyncSession, tenant: Tenant
) -> None:
    await _add_product(db_session, tenant.id, name="Aluminiumprofil 40x40", category="Profile")

    found = await search_products(
        db_session, tenant_id=tenant.id, query_text="Bitte Angebot über Aluminiumprofile"
    )

    assert [p.name for p in found] == ["Aluminiumprofil 40x40"]


async def test_compound_plural_matches(db_session: AsyncSession, tenant: Tenant) -> None:
    await _add_product(db_session, tenant.id, name="Edelstahlschraube M8")

    found = await search_products(
        db_session, tenant_id=tenant.id, query_text="Wir brauchen Edelstahlschrauben M8"
    )

    assert [p.name for p in found] == ["Edelstahlschraube M8"]


async def test_a_word_short_enough_to_escape_the_old_stemmer_matches(
    db_session: AsyncSession, tenant: Tenant
) -> None:
    """Words of five characters or fewer were never stemmed at all."""
    await _add_product(db_session, tenant.id, name="Kabelbinder 200mm")

    found = await search_products(db_session, tenant_id=tenant.id, query_text="Brauchen wir Kabelbinder?")

    assert [p.name for p in found] == ["Kabelbinder 200mm"]


async def test_a_meaningful_word_that_was_a_hardcoded_stopword_now_matches(
    db_session: AsyncSession, tenant: Tenant
) -> None:
    """"zwei" was in the hardcoded stopword list, so a product whose name
    depends on it could not be found by that word."""
    await _add_product(db_session, tenant.id, name="Zwei-Komponenten-Kleber", category="Klebstoffe")

    found = await search_products(
        db_session, tenant_id=tenant.id, query_text="Suchen einen Zwei-Komponenten-Kleber"
    )

    assert [p.name for p in found] == ["Zwei-Komponenten-Kleber"]


async def test_a_greetings_only_mail_matches_nothing(
    db_session: AsyncSession, tenant: Tenant
) -> None:
    await _add_product(db_session, tenant.id, name="Motor MX-200")

    found = await search_products(
        db_session, tenant_id=tenant.id, query_text="Hallo, vielen Dank und viele Grüße"
    )

    assert found == []


async def test_empty_query_text_returns_nothing(db_session: AsyncSession, tenant: Tenant) -> None:
    await _add_product(db_session, tenant.id, name="Motor MX-200")
    assert await search_products(db_session, tenant_id=tenant.id, query_text="   ") == []


async def test_a_name_hit_outranks_a_description_only_hit(
    db_session: AsyncSession, tenant: Tenant
) -> None:
    """A product *called* "Aluminiumprofil" answers a request for aluminium
    profiles better than one that merely mentions them in prose."""
    await _add_product(
        db_session, tenant.id, name="Montageset", description="Passend für Aluminiumprofile aller Art"
    )
    await _add_product(db_session, tenant.id, name="Aluminiumprofil 40x40")

    found = await search_products(
        db_session, tenant_id=tenant.id, query_text="Angebot über Aluminiumprofil"
    )

    assert found[0].name == "Aluminiumprofil 40x40"


async def test_search_respects_the_limit(db_session: AsyncSession, tenant: Tenant) -> None:
    for i in range(8):
        await _add_product(db_session, tenant.id, name=f"Motor MX-{i}", category="Antriebe")

    found = await search_products(db_session, tenant_id=tenant.id, query_text="Motor", limit=3)

    assert len(found) == 3


# --- catalog browsing (substring), kept separate on purpose -------------


async def test_browse_finds_a_partial_word_that_full_text_search_would_not(
    db_session: AsyncSession, tenant: Tenant
) -> None:
    """A person typing in a filter box expects substring behaviour; an
    inquiry mail needs stemmed relevance. Conflating them makes one wrong."""
    await _add_product(db_session, tenant.id, name="Aluminiumprofil 40x40", sku="ALU-4711")

    assert len(await search_products_by_text(db_session, tenant_id=tenant.id, q="minium")) == 1
    assert len(await search_products_by_text(db_session, tenant_id=tenant.id, q="4711")) == 1


async def test_browse_filters_by_category(db_session: AsyncSession, tenant: Tenant) -> None:
    await _add_product(db_session, tenant.id, name="Motor", category="Antriebe")
    await _add_product(db_session, tenant.id, name="Schraube", category="Verbindungselemente")

    found = await search_products_by_text(db_session, tenant_id=tenant.id, category="Antriebe")

    assert [p.name for p in found] == ["Motor"]


async def test_browse_is_tenant_scoped(db_session: AsyncSession, tenant: Tenant) -> None:
    other = Tenant(name="Fremd", slug=f"fremd-{uuid.uuid4().hex[:8]}")
    db_session.add(other)
    await db_session.flush()
    await _add_product(db_session, other.id, name="Fremdprodukt")

    assert await search_products_by_text(db_session, tenant_id=tenant.id) == []
