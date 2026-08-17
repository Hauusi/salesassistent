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
from app.services.product_search import extract_keywords, format_products_for_prompt, search_products


def test_extract_keywords_drops_stopwords_and_short_tokens() -> None:
    text = "Hallo, wir benötigen ein Angebot für 200 Stück Aluminiumprofile Typ AP-40, Länge 3m."
    keywords = extract_keywords(text)

    assert "aluminiumprofile" in keywords
    assert "ap-40" in keywords
    # stopwords / filler / too-short tokens must not show up
    for noise in ("wir", "für", "ein", "3m"):
        assert noise not in keywords


def test_extract_keywords_deduplicates_preserving_order() -> None:
    keywords = extract_keywords("Profil Profil Verbinder profil")
    assert keywords == ["profil", "verbinder"]


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
