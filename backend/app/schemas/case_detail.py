from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, field_validator

from app.models.enums import CaseStatus, DealStage
from app.schemas.common import ContactOut, ORMBase
from app.schemas.email import EmailOut

# Only these may be set through PATCH /api/cases/{id}/stage - see
# app/services/case_stage_service.py. ANFRAGE/ANGEBOT_ERSTELLT/NACHFASSEN
# are pipeline-driven only; letting a client set them by hand would let a
# stray API call fight the automatic transitions.
_MANUALLY_SETTABLE_STAGES = frozenset({DealStage.GEWONNEN, DealStage.VERLOREN})


class CaseListItemOut(ORMBase):
    # No defaults on these: the route always populates them, so declaring
    # them required is the accurate contract. A default would render them
    # as optional in the OpenAPI document, which propagates into the
    # generated frontend types as `T[] | undefined` and forces every call
    # site to guard against a case that cannot occur.
    id: uuid.UUID
    title: str
    summary: str | None
    status: CaseStatus
    deal_stage: DealStage
    deal_stage_changed_at: datetime
    created_at: datetime
    contacts: list[ContactOut]
    email_count: int


class CaseDetailOut(CaseListItemOut):
    emails: list[EmailOut]


class CaseStageUpdateIn(BaseModel):
    deal_stage: DealStage

    @field_validator("deal_stage")
    @classmethod
    def _only_manual_stages(cls, value: DealStage) -> DealStage:
        if value not in _MANUALLY_SETTABLE_STAGES:
            allowed = ", ".join(s.value for s in _MANUALLY_SETTABLE_STAGES)
            raise ValueError(
                f"deal_stage '{value.value}' kann nicht manuell gesetzt werden - "
                f"erlaubt sind nur: {allowed}."
            )
        return value
