"""Tests for app.services.case_stage_service - the automatic deal-stage
transitions. ANFRAGE and ANGEBOT_ERSTELLT are exercised elsewhere (case
creation and tests/test_drafts_api.py respectively); this covers the one
transition with its own dedicated logic: the NACHFASSEN sweep, which fires
on the *absence* of a reply rather than on an event.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.case import Case
from app.models.email_message import EmailMessage
from app.models.enums import DealStage
from app.models.mailbox import Mailbox
from app.models.tenant import Tenant
from app.models.user import User
from app.services.case_stage_service import apply_stale_offer_transitions

settings = get_settings()


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
    return {"tenant": tenant, "mailbox": mailbox}


async def _make_case(
    db_session: AsyncSession, tenant_id, *, stage: DealStage, changed_days_ago: float
) -> Case:
    case = Case(tenant_id=tenant_id, title="Testcase", deal_stage=stage)
    db_session.add(case)
    await db_session.flush()
    # Overwrite the server_default `now()` with a backdated timestamp so
    # the case looks like its stage changed `changed_days_ago` days ago.
    case.deal_stage_changed_at = datetime.now(UTC) - timedelta(days=changed_days_ago)
    await db_session.flush()
    return case


async def test_a_stale_offer_moves_to_nachfassen(db_session: AsyncSession, seed: dict) -> None:
    case = await _make_case(
        db_session,
        seed["tenant"].id,
        stage=DealStage.ANGEBOT_ERSTELLT,
        changed_days_ago=settings.case_followup_threshold_days + 1,
    )

    moved = await apply_stale_offer_transitions(db_session)

    assert moved == 1
    # No db_session.refresh() here: apply_stale_offer_transitions()
    # deliberately doesn't commit (the caller owns that), so a refresh
    # would just reload the still-unchanged committed row and overwrite
    # the pending in-memory mutation. `case` is the same Python object the
    # sweep queried and mutated (same session, same identity map), so its
    # attribute already reflects the transition.
    assert case.deal_stage is DealStage.NACHFASSEN


async def test_a_case_still_within_the_threshold_is_left_alone(
    db_session: AsyncSession, seed: dict
) -> None:
    case = await _make_case(
        db_session,
        seed["tenant"].id,
        stage=DealStage.ANGEBOT_ERSTELLT,
        changed_days_ago=settings.case_followup_threshold_days - 1,
    )

    moved = await apply_stale_offer_transitions(db_session)

    assert moved == 0
    await db_session.refresh(case)
    assert case.deal_stage is DealStage.ANGEBOT_ERSTELLT


async def test_a_reply_since_the_offer_blocks_the_nachfassen_transition(
    db_session: AsyncSession, seed: dict
) -> None:
    """The customer already replied - a human just hasn't answered yet.
    That's a different problem than a silently unanswered offer, so this
    must not be relabeled as stale (see apply_stale_offer_transitions's
    docstring)."""
    case = await _make_case(
        db_session,
        seed["tenant"].id,
        stage=DealStage.ANGEBOT_ERSTELLT,
        changed_days_ago=settings.case_followup_threshold_days + 1,
    )
    reply = EmailMessage(
        tenant_id=seed["tenant"].id,
        mailbox_id=seed["mailbox"].id,
        case_id=case.id,
        gmail_message_id=f"gm-{uuid.uuid4().hex[:8]}",
        sender_address="kunde@example.com",
        raw_content="Danke, klingt gut.",
        received_at=datetime.now(UTC) - timedelta(hours=1),
    )
    db_session.add(reply)
    await db_session.flush()

    moved = await apply_stale_offer_transitions(db_session)

    assert moved == 0
    await db_session.refresh(case)
    assert case.deal_stage is DealStage.ANGEBOT_ERSTELLT
