"""Tests for PATCH /api/cases/{id}/stage - manually marking a case
GEWONNEN or VERLOREN, the only two stages a human sets directly (see
app/services/case_stage_service.py for why every other stage is
pipeline-driven), plus the deal_stage filter on GET /api/cases.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.action_log import ActionLog
from app.models.case import Case
from app.models.enums import DealStage
from app.models.tenant import Tenant


@pytest.fixture
async def case(db_session: AsyncSession, tenant: Tenant) -> Case:
    case = Case(tenant_id=tenant.id, title="Testcase")
    db_session.add(case)
    await db_session.commit()
    return case


async def test_marking_a_case_gewonnen(
    api_client, db_session: AsyncSession, case: Case
) -> None:
    response = await api_client.patch(
        f"/api/cases/{case.id}/stage", json={"deal_stage": "gewonnen"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["deal_stage"] == "gewonnen"
    await db_session.refresh(case)
    assert case.deal_stage is DealStage.GEWONNEN


async def test_marking_a_case_verloren(
    api_client, db_session: AsyncSession, case: Case
) -> None:
    response = await api_client.patch(
        f"/api/cases/{case.id}/stage", json={"deal_stage": "verloren"}
    )

    assert response.status_code == 200
    await db_session.refresh(case)
    assert case.deal_stage is DealStage.VERLOREN


async def test_setting_stage_is_logged_to_the_audit_trail(
    api_client, db_session: AsyncSession, case: Case
) -> None:
    await api_client.patch(f"/api/cases/{case.id}/stage", json={"deal_stage": "gewonnen"})

    actions = (
        await db_session.execute(select(ActionLog.action).where(ActionLog.entity_id == case.id))
    ).scalars().all()
    assert "deal_stage_changed" in actions


@pytest.mark.parametrize("stage", ["anfrage", "angebot_erstellt", "nachfassen"])
async def test_a_pipeline_only_stage_cannot_be_set_manually(
    api_client, case: Case, stage: str
) -> None:
    """ANFRAGE/ANGEBOT_ERSTELLT/NACHFASSEN are set by the pipeline itself -
    a stray API call must not be able to fight those transitions."""
    response = await api_client.patch(f"/api/cases/{case.id}/stage", json={"deal_stage": stage})
    assert response.status_code == 422


async def test_a_human_can_correct_a_terminal_stage(
    api_client, db_session: AsyncSession, case: Case
) -> None:
    """force=True on the manual endpoint: a mistaken Verloren must stay
    fixable back to Gewonnen (or vice versa)."""
    await api_client.patch(f"/api/cases/{case.id}/stage", json={"deal_stage": "verloren"})

    response = await api_client.patch(
        f"/api/cases/{case.id}/stage", json={"deal_stage": "gewonnen"}
    )

    assert response.status_code == 200
    await db_session.refresh(case)
    assert case.deal_stage is DealStage.GEWONNEN


async def test_setting_stage_on_an_unknown_case_is_a_404(api_client, tenant: Tenant) -> None:
    # `tenant` (and thus its user) must exist, otherwise get_current_user
    # itself returns 409 before the handler ever looks up the case - see
    # test_approving_without_a_connected_mailbox_is_a_conflict for the same
    # pattern on the drafts endpoint.
    response = await api_client.patch(
        f"/api/cases/{uuid.uuid4()}/stage", json={"deal_stage": "gewonnen"}
    )
    assert response.status_code == 404


async def test_list_cases_filters_by_deal_stage(
    api_client, db_session: AsyncSession, tenant: Tenant, case: Case
) -> None:
    other = Case(tenant_id=tenant.id, title="Anderer Case")
    db_session.add(other)
    await db_session.commit()

    await api_client.patch(f"/api/cases/{case.id}/stage", json={"deal_stage": "gewonnen"})

    response = await api_client.get("/api/cases", params={"deal_stage": "gewonnen"})

    assert response.status_code == 200
    ids = [c["id"] for c in response.json()]
    assert ids == [str(case.id)]
