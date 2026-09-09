"""Deal-stage transitions for Case - the sales-pipeline view of a case,
distinct from CaseStatus (offen/geschlossen).

Three of the five stages are event-driven and set at their event's own
call site, not here:

- ANFRAGE: the default for a newly created case (see
  app/services/pipeline.py::_get_or_create_case) - a case only ever gets
  created when a mail didn't match an existing one, i.e. exactly the "new
  inquiry" moment.
- ANGEBOT_ERSTELLT: set when a draft is approved and actually sent (see
  app/api/routes/drafts.py::approve_draft).
- GEWONNEN/VERLOREN: set only by an explicit human action (PATCH
  /api/cases/{id}/stage, see app/api/routes/cases.py) - never by any
  automatic transition.

NACHFASSEN is different: it fires on the *absence* of an event (no reply
within N days), which nothing "happens" to trigger. apply_stale_offer_
transitions() below is the periodic sweep for that, called from the
scheduler tick (see app/workers/run_scheduler.py) - the same process that
already does periodic background work in this codebase.

set_deal_stage() is the one place every transition goes through, so
deal_stage and deal_stage_changed_at can never drift apart, and a
manually-closed deal (GEWONNEN/VERLOREN) can't be silently reopened by an
automatic transition.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.case import Case
from app.models.email_message import EmailMessage
from app.models.enums import DealStage

# Only the manual PATCH endpoint may move a case out of these - see
# set_deal_stage's `force` parameter.
_TERMINAL_STAGES = frozenset({DealStage.GEWONNEN, DealStage.VERLOREN})


def set_deal_stage(case: Case, stage: DealStage, *, force: bool = False) -> bool:
    """Moves `case` to `stage`, updating deal_stage_changed_at alongside it.

    Refuses to move a case out of a manually-closed stage (GEWONNEN/
    VERLOREN) unless `force=True` - only the explicit PATCH
    /api/cases/{id}/stage endpoint passes that; every automatic transition
    leaves a closed deal alone.

    Returns whether the stage actually changed, so a caller that only
    wants to log/react on a real transition doesn't have to duplicate the
    comparison. Does not commit - the caller owns the transaction
    boundary, same as every other write in this codebase.
    """
    if case.deal_stage == stage:
        return False
    if not force and case.deal_stage in _TERMINAL_STAGES:
        return False
    case.deal_stage = stage
    case.deal_stage_changed_at = datetime.now(UTC)
    return True


async def apply_stale_offer_transitions(db: AsyncSession) -> int:
    """Moves every case still in ANGEBOT_ERSTELLT, with no incoming mail
    since the offer went out, to NACHFASSEN once
    CASE_FOLLOWUP_THRESHOLD_DAYS have passed. Returns how many transitioned.

    "No incoming mail since the offer went out" is checked explicitly
    (not just "time elapsed") so a case where the customer already replied
    - and a human just hasn't answered that reply yet - is not mislabeled
    as a stale, unanswered offer; that is a different problem, already
    visible as an open case with a recent inbound mail.

    Does not commit - the caller owns the transaction boundary.
    """
    cutoff = datetime.now(UTC) - timedelta(days=get_settings().case_followup_threshold_days)

    reply_since_offer = (
        select(EmailMessage.id)
        .where(
            EmailMessage.case_id == Case.id,
            EmailMessage.received_at > Case.deal_stage_changed_at,
        )
        .exists()
    )

    result = await db.execute(
        select(Case).where(
            Case.deal_stage == DealStage.ANGEBOT_ERSTELLT,
            Case.deal_stage_changed_at < cutoff,
            ~reply_since_offer,
        )
    )
    stale_cases = list(result.scalars().all())
    for case in stale_cases:
        set_deal_stage(case, DealStage.NACHFASSEN)
    return len(stale_cases)
