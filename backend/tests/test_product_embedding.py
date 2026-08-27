"""Tests for app.services.product_embedding: the one place a product's
embedding is computed - never at search time (see
app/services/product_search.py).
"""
from __future__ import annotations

import pytest

from app.models.product import Product
from app.services import embeddings, product_embedding


@pytest.fixture(autouse=True)
def _fake_embed(monkeypatch):
    calls: list[str] = []

    async def _embed_text(text: str, *, client=None) -> list[float]:
        calls.append(text)
        return [0.1] * 1024

    monkeypatch.setattr(embeddings, "embed_text", _embed_text)
    return calls


def _product(**overrides) -> Product:
    defaults = {"name": "Aluminiumprofil 40x40", "category": "Profile", "description": "Eloxiert"}
    defaults.update(overrides)
    return Product(**defaults)


async def test_set_product_embedding_computes_from_name_category_description(
    _fake_embed: list[str],
) -> None:
    product = _product()

    await product_embedding.set_product_embedding(product)

    assert product.embedding == [0.1] * 1024
    assert len(_fake_embed) == 1
    text = _fake_embed[0]
    assert "Aluminiumprofil 40x40" in text
    assert "Profile" in text
    assert "Eloxiert" in text


async def test_set_product_embedding_tolerates_missing_optional_fields(
    _fake_embed: list[str],
) -> None:
    product = _product(category=None, description=None)

    await product_embedding.set_product_embedding(product)

    assert product.embedding is not None
    assert _fake_embed[0] == "Aluminiumprofil 40x40"


@pytest.mark.parametrize(
    "changed_fields,expected",
    [
        ({"name"}, True),
        ({"category"}, True),
        ({"description"}, True),
        ({"price"}, False),
        ({"availability", "specs"}, False),
        ({"price", "name"}, True),
        (set(), False),
    ],
)
def test_fields_affect_embedding(changed_fields: set[str], expected: bool) -> None:
    assert product_embedding.fields_affect_embedding(changed_fields) is expected
