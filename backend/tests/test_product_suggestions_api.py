"""End-to-end tests for the product-suggestion review workflow: a
suggestion never becomes a Product on its own - see
app/api/routes/product_suggestions.py and app/models/product_suggestion.py.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email_message import EmailMessage
from app.models.enums import ProductSuggestionStatus
from app.models.mailbox import Mailbox
from app.models.product import Product
from app.models.product_suggestion import ProductSuggestion
from app.models.tenant import Tenant
from app.models.user import User


@pytest.fixture
async def suggestion(db_session: AsyncSession, tenant: Tenant) -> ProductSuggestion:
    user_id = (await db_session.execute(select(User.id).limit(1))).scalar_one_or_none()
    if user_id is None:
        user = User(tenant_id=tenant.id, email="user@example.com")
        db_session.add(user)
        await db_session.flush()
        user_id = user.id

    mailbox = Mailbox(tenant_id=tenant.id, user_id=user_id, email_address="me@example.com")
    db_session.add(mailbox)
    await db_session.flush()

    email = EmailMessage(
        tenant_id=tenant.id,
        mailbox_id=mailbox.id,
        gmail_message_id=f"gm-{uuid.uuid4().hex[:8]}",
        subject="Neues Produkt",
        sender_address="lieferant@example.com",
        sender_name="Lieferant",
        raw_content="Artikelnummer ALU-9090, Aluminiumprofil 90x90.",
        received_at=datetime.now(UTC),
    )
    db_session.add(email)
    await db_session.flush()

    suggestion = ProductSuggestion(
        tenant_id=tenant.id,
        email_message_id=email.id,
        sku="ALU-9090",
        name="Aluminiumprofil 90x90",
        description="Eloxiert, Nutbreite 10mm, für Maschinenbau-Rahmen.",
    )
    db_session.add(suggestion)
    await db_session.commit()
    return suggestion


async def test_list_returns_only_pending_suggestions_by_default(
    api_client, suggestion: ProductSuggestion
) -> None:
    response = await api_client.get("/api/product-suggestions")
    assert response.status_code == 200
    assert [s["id"] for s in response.json()] == [str(suggestion.id)]
    assert response.json()[0]["email_message"]["subject"] == "Neues Produkt"


async def test_approve_creates_a_real_product_and_marks_the_suggestion(
    api_client, db_session: AsyncSession, suggestion: ProductSuggestion
) -> None:
    response = await api_client.post(f"/api/product-suggestions/{suggestion.id}/approve")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "freigegeben"
    assert body["created_product_id"] is not None

    product = await db_session.get(Product, uuid.UUID(body["created_product_id"]))
    assert product is not None
    assert product.sku == "ALU-9090"
    assert product.name == "Aluminiumprofil 90x90"
    assert product.description == "Eloxiert, Nutbreite 10mm, für Maschinenbau-Rahmen."
    assert product.tenant_id == suggestion.tenant_id

    await db_session.refresh(suggestion)
    assert suggestion.status is ProductSuggestionStatus.FREIGEGEBEN
    assert suggestion.reviewed_at is not None
    assert suggestion.created_product_id == product.id


async def test_approve_removes_the_suggestion_from_the_open_list(
    api_client, suggestion: ProductSuggestion
) -> None:
    await api_client.post(f"/api/product-suggestions/{suggestion.id}/approve")

    response = await api_client.get("/api/product-suggestions")
    assert response.json() == []


async def test_reject_marks_the_suggestion_and_creates_no_product(
    api_client, db_session: AsyncSession, suggestion: ProductSuggestion
) -> None:
    response = await api_client.post(
        f"/api/product-suggestions/{suggestion.id}/reject",
        json={"reason": "Artikel wird nicht geführt"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "abgelehnt"

    await db_session.refresh(suggestion)
    assert suggestion.status is ProductSuggestionStatus.ABGELEHNT
    assert suggestion.rejected_reason == "Artikel wird nicht geführt"
    assert suggestion.created_product_id is None

    products = (await db_session.execute(select(Product))).scalars().all()
    assert products == []


async def test_a_second_approval_is_rejected(api_client, suggestion: ProductSuggestion) -> None:
    first = await api_client.post(f"/api/product-suggestions/{suggestion.id}/approve")
    second = await api_client.post(f"/api/product-suggestions/{suggestion.id}/approve")

    assert first.status_code == 200
    assert second.status_code == 409


async def test_approving_a_rejected_suggestion_is_a_conflict(
    api_client, db_session: AsyncSession, suggestion: ProductSuggestion
) -> None:
    suggestion.status = ProductSuggestionStatus.ABGELEHNT
    await db_session.commit()

    response = await api_client.post(f"/api/product-suggestions/{suggestion.id}/approve")
    assert response.status_code == 409


async def test_approving_an_unknown_suggestion_is_a_404(api_client, tenant: Tenant) -> None:
    response = await api_client.post(f"/api/product-suggestions/{uuid.uuid4()}/approve")
    assert response.status_code == 404


async def test_approve_conflicts_if_the_sku_was_added_to_the_catalog_meanwhile(
    api_client, db_session: AsyncSession, suggestion: ProductSuggestion
) -> None:
    """The catalog is the source of truth for SKU uniqueness - a manual add
    (or another suggestion) landing the same SKU first must not be
    silently overwritten or duplicated."""
    db_session.add(
        Product(tenant_id=suggestion.tenant_id, name="Manuell angelegt", sku=suggestion.sku)
    )
    await db_session.commit()

    response = await api_client.post(f"/api/product-suggestions/{suggestion.id}/approve")
    assert response.status_code == 409

    products = (await db_session.execute(select(Product))).scalars().all()
    assert len(products) == 1, "must not create a duplicate-SKU product"
