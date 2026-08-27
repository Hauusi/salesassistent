"""Tests for the product catalog endpoints, including CSV import.

The import is the one place a user hands the system a file, and the
content-type it arrives with is decided by their browser - not by them and
not by us.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import Product
from app.models.tenant import Tenant
from app.services import embeddings


@pytest.fixture(autouse=True)
def _stub_product_embeddings(monkeypatch) -> list[str]:
    """Every create/update/import call now computes an embedding (see
    app/services/product_embedding.py) - stubbed here so these tests cover
    the catalog CRUD/import contract without a real Voyage call. Returns
    the list of embedded texts, so a test can assert on call count/content."""
    calls: list[str] = []

    async def _embed_text(text: str, *, client=None) -> list[float]:
        calls.append(text)
        return [0.1] * 1024

    monkeypatch.setattr(embeddings, "embed_text", _embed_text)
    return calls

_CSV = (
    "name,description,category,sku,price,currency,availability,specs\n"
    "Aluminiumprofil 40x40,Strangpressprofil,Profile,ALU-4040,12.50,EUR,3-5 Werktage,"
    '"{""Gewicht"": ""2.4 kg""}"\n'
    "Motor MX-200,Elektromotor,Antriebe,MX-200,349,EUR,ab Lager,{}\n"
)


async def _import(api_client, content: str, content_type: str | None = "text/csv"):
    files = {"file": ("katalog.csv", content.encode("utf-8"), content_type)}
    return await api_client.post("/api/knowledge/products/import", files=files)


async def test_import_creates_products(api_client, tenant: Tenant, db_session: AsyncSession) -> None:
    response = await _import(api_client, _CSV)

    assert response.status_code == 200, response.text
    assert response.json()["created"] == 2

    products = (await db_session.execute(select(Product))).scalars().all()
    assert {p.name for p in products} == {"Aluminiumprofil 40x40", "Motor MX-200"}
    alu = next(p for p in products if p.sku == "ALU-4040")
    assert str(alu.price) == "12.50"
    assert alu.specs == {"Gewicht": "2.4 kg"}


@pytest.mark.parametrize(
    "content_type",
    [
        "text/csv",
        "application/vnd.ms-excel",  # Windows
        "text/plain",  # Firefox on Linux
        "application/octet-stream",
        None,
    ],
)
async def test_import_accepts_the_content_types_browsers_actually_send(
    api_client, tenant: Tenant, content_type: str | None
) -> None:
    """Regression: text/plain was rejected, which is what Firefox on Linux
    commonly sends for a .csv - a false alarm on a perfectly good file."""
    response = await _import(api_client, _CSV, content_type)
    assert response.status_code == 200, response.text


async def test_import_rejects_an_obviously_wrong_file_type(api_client, tenant: Tenant) -> None:
    files = {"file": ("bild.png", b"\x89PNG\r\n\x1a\n", "image/png")}
    response = await api_client.post("/api/knowledge/products/import", files=files)
    assert response.status_code == 400


async def test_import_rejects_an_empty_file(api_client, tenant: Tenant) -> None:
    response = await _import(api_client, "   ")
    assert response.status_code == 400


async def test_import_reports_a_missing_required_column(api_client, tenant: Tenant) -> None:
    response = await _import(api_client, "beschreibung,preis\nfoo,1\n")
    assert response.status_code == 200
    assert response.json()["errors"]


async def test_reimporting_the_same_sku_updates_instead_of_duplicating(
    api_client, tenant: Tenant, db_session: AsyncSession
) -> None:
    await _import(api_client, _CSV)
    updated_csv = _CSV.replace("12.50", "13.90")
    response = await _import(api_client, updated_csv)

    assert response.json()["updated"] == 2
    assert response.json()["created"] == 0

    alu = (
        await db_session.execute(select(Product).where(Product.sku == "ALU-4040"))
    ).scalar_one()
    assert str(alu.price) == "13.90"


async def test_import_reports_a_row_with_invalid_specs_without_failing_the_import(
    api_client, tenant: Tenant
) -> None:
    csv = "name,specs\nProdukt A,{kaputt\nProdukt B,{}\n"
    response = await _import(api_client, csv)

    body = response.json()
    assert body["created"] == 2
    assert any("specs" in error for error in body["errors"])


async def test_products_can_be_filtered_server_side(
    api_client, tenant: Tenant, db_session: AsyncSession
) -> None:
    """The UI used to ignore these parameters and re-filter client-side
    against a truncated list."""
    await _import(api_client, _CSV)

    by_query = await api_client.get("/api/knowledge/products?q=Alumin")
    assert [p["name"] for p in by_query.json()] == ["Aluminiumprofil 40x40"]

    by_category = await api_client.get("/api/knowledge/products?category=Antriebe")
    assert [p["name"] for p in by_category.json()] == ["Motor MX-200"]


async def test_product_crud_roundtrip(api_client, tenant: Tenant) -> None:
    created = await api_client.post(
        "/api/knowledge/products",
        json={"name": "Testprodukt", "price": "9.99", "currency": "EUR", "specs": {}},
    )
    assert created.status_code == 201
    product_id = created.json()["id"]

    updated = await api_client.put(
        f"/api/knowledge/products/{product_id}", json={"availability": "auf Anfrage"}
    )
    assert updated.status_code == 200
    assert updated.json()["availability"] == "auf Anfrage"
    assert updated.json()["name"] == "Testprodukt", "a partial update must not clear other fields"

    deleted = await api_client.delete(f"/api/knowledge/products/{product_id}")
    assert deleted.status_code == 204

    assert await api_client.get(f"/api/knowledge/products/{product_id}") is not None
    gone = await api_client.put(
        f"/api/knowledge/products/{product_id}", json={"name": "Weg"}
    )
    assert gone.status_code == 404


async def test_creating_a_product_computes_its_embedding(
    api_client, db_session: AsyncSession, tenant: Tenant, _stub_product_embeddings: list[str]
) -> None:
    created = await api_client.post(
        "/api/knowledge/products",
        json={"name": "Lichtleiste LED", "description": "Lineares LED-Leuchtmittel"},
    )
    assert created.status_code == 201

    product = await db_session.get(Product, created.json()["id"])
    assert product.embedding is not None
    assert len(_stub_product_embeddings) == 1
    assert "Lichtleiste LED" in _stub_product_embeddings[0]
    assert "Lineares LED-Leuchtmittel" in _stub_product_embeddings[0]


async def test_updating_an_unrelated_field_does_not_recompute_the_embedding(
    api_client, tenant: Tenant, _stub_product_embeddings: list[str]
) -> None:
    """price/availability/specs don't feed the embedding text - recomputing
    on every edit would spend a Voyage call for no reason (see
    app/services/product_embedding.py)."""
    created = await api_client.post(
        "/api/knowledge/products", json={"name": "Testprodukt", "price": "9.99"}
    )
    product_id = created.json()["id"]
    assert len(_stub_product_embeddings) == 1  # from the create above

    updated = await api_client.put(
        f"/api/knowledge/products/{product_id}", json={"price": "12.00", "availability": "Auf Lager"}
    )
    assert updated.status_code == 200
    assert len(_stub_product_embeddings) == 1, "an unrelated-field edit must not recompute"


async def test_updating_the_description_recomputes_the_embedding(
    api_client, db_session: AsyncSession, tenant: Tenant, _stub_product_embeddings: list[str]
) -> None:
    created = await api_client.post("/api/knowledge/products", json={"name": "Testprodukt"})
    product_id = created.json()["id"]
    assert len(_stub_product_embeddings) == 1

    updated = await api_client.put(
        f"/api/knowledge/products/{product_id}", json={"description": "Neue Beschreibung"}
    )
    assert updated.status_code == 200
    assert len(_stub_product_embeddings) == 2
    assert "Neue Beschreibung" in _stub_product_embeddings[1]


async def test_csv_import_computes_an_embedding_per_row(
    api_client, tenant: Tenant, _stub_product_embeddings: list[str]
) -> None:
    response = await _import(api_client, _CSV)
    assert response.status_code == 200, response.text
    assert len(_stub_product_embeddings) == 2  # one per CSV row


async def test_knowledge_search_only_returns_information_mails(
    api_client, tenant: Tenant
) -> None:
    response = await api_client.get("/api/knowledge/search")
    assert response.status_code == 200
    assert response.json() == []
