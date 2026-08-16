"""Tests for app.services.case_matching against a real Postgres+pgvector
database (see tests/conftest.py) - this logic lives in a SQL query, so a
DB-free unit test wouldn't actually exercise it.
"""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.case import Case
from app.models.contact import Contact
from app.models.email_message import EmailMessage
from app.models.mailbox import Mailbox
from app.models.tenant import Tenant
from app.models.user import User
from app.services.case_matching import find_matching_case

settings = get_settings()


def make_embedding(angle_degrees: float) -> list[float]:
    """A vector in the plane spanned by the first two dimensions, angled
    `angle_degrees` from the reference (1, 0, 0, ...) vector - so cosine
    similarity to the reference is exactly cos(angle_degrees)."""
    radians = math.radians(angle_degrees)
    vec = [0.0] * settings.embedding_dimensions
    vec[0] = math.cos(radians)
    vec[1] = math.sin(radians)
    return vec


@pytest.fixture
async def seed(db_session: AsyncSession):
    tenant = Tenant(name="Test", slug=f"test-{uuid.uuid4().hex[:8]}")
    db_session.add(tenant)
    await db_session.flush()

    user = User(tenant_id=tenant.id, email="user@example.com")
    db_session.add(user)
    await db_session.flush()

    mailbox = Mailbox(tenant_id=tenant.id, user_id=user.id, email_address="me@example.com")
    db_session.add(mailbox)
    await db_session.flush()

    contact_a = Contact(tenant_id=tenant.id, email_address="a@customer.de", name="Kontakt A")
    contact_b = Contact(tenant_id=tenant.id, email_address="b@customer.de", name="Kontakt B")
    db_session.add_all([contact_a, contact_b])
    await db_session.flush()

    return {"tenant": tenant, "mailbox": mailbox, "contact_a": contact_a, "contact_b": contact_b}


async def _add_cased_email(
    db_session: AsyncSession, *, tenant_id, mailbox_id, contact_id, case_id, angle: float, days_ago: int = 1
) -> None:
    email = EmailMessage(
        tenant_id=tenant_id,
        mailbox_id=mailbox_id,
        contact_id=contact_id,
        case_id=case_id,
        gmail_message_id=f"gm-{uuid.uuid4().hex}",
        sender_address="a@customer.de",
        raw_content="Testinhalt",
        wichtigkeits_kategorie=None,
        embedding=make_embedding(angle),
        received_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
    )
    db_session.add(email)
    await db_session.flush()


async def test_same_contact_close_topic_matches_existing_case(db_session: AsyncSession, seed: dict) -> None:
    tenant, mailbox, contact_a = seed["tenant"], seed["mailbox"], seed["contact_a"]
    case = Case(tenant_id=tenant.id, title="Bestehender Case")
    db_session.add(case)
    await db_session.flush()
    await _add_cased_email(
        db_session, tenant_id=tenant.id, mailbox_id=mailbox.id, contact_id=contact_a.id, case_id=case.id, angle=0
    )

    # 15 degrees off -> cos(15°) ≈ 0.966, well above the default 0.78 threshold.
    match = await find_matching_case(
        db_session, tenant_id=tenant.id, contact_id=contact_a.id, embedding=make_embedding(15)
    )

    assert match is not None
    assert match.case.id == case.id
    assert match.similarity == pytest.approx(math.cos(math.radians(15)), abs=1e-3)


async def test_unrelated_topic_does_not_match(db_session: AsyncSession, seed: dict) -> None:
    tenant, mailbox, contact_a = seed["tenant"], seed["mailbox"], seed["contact_a"]
    case = Case(tenant_id=tenant.id, title="Bestehender Case")
    db_session.add(case)
    await db_session.flush()
    await _add_cased_email(
        db_session, tenant_id=tenant.id, mailbox_id=mailbox.id, contact_id=contact_a.id, case_id=case.id, angle=0
    )

    # 90 degrees off -> cosine similarity 0, clearly below threshold.
    match = await find_matching_case(
        db_session, tenant_id=tenant.id, contact_id=contact_a.id, embedding=make_embedding(90)
    )

    assert match is None


async def test_new_contact_matches_case_via_tenant_wide_semantic_search(
    db_session: AsyncSession, seed: dict
) -> None:
    """Supports a Case with multiple contacts: a second person at the same
    customer writing about the same topic should attach to the existing
    case even though *they* have no history yet."""
    tenant, mailbox, contact_a, contact_b = (
        seed["tenant"],
        seed["mailbox"],
        seed["contact_a"],
        seed["contact_b"],
    )
    case = Case(tenant_id=tenant.id, title="Bestehender Case")
    db_session.add(case)
    await db_session.flush()
    await _add_cased_email(
        db_session, tenant_id=tenant.id, mailbox_id=mailbox.id, contact_id=contact_a.id, case_id=case.id, angle=0
    )

    match = await find_matching_case(
        db_session, tenant_id=tenant.id, contact_id=contact_b.id, embedding=make_embedding(10)
    )

    assert match is not None
    assert match.case.id == case.id


async def test_old_correspondence_outside_lookback_window_is_ignored(
    db_session: AsyncSession, seed: dict
) -> None:
    tenant, mailbox, contact_a = seed["tenant"], seed["mailbox"], seed["contact_a"]
    case = Case(tenant_id=tenant.id, title="Alter Case")
    db_session.add(case)
    await db_session.flush()
    await _add_cased_email(
        db_session,
        tenant_id=tenant.id,
        mailbox_id=mailbox.id,
        contact_id=contact_a.id,
        case_id=case.id,
        angle=0,
        days_ago=settings.case_lookback_days + 30,
    )

    match = await find_matching_case(
        db_session, tenant_id=tenant.id, contact_id=contact_a.id, embedding=make_embedding(0)
    )

    assert match is None


async def test_no_existing_cases_returns_none(db_session: AsyncSession, seed: dict) -> None:
    match = await find_matching_case(
        db_session, tenant_id=seed["tenant"].id, contact_id=seed["contact_a"].id, embedding=make_embedding(0)
    )
    assert match is None
