"""Case assignment: attach an incoming mail to an existing Case, or signal
that a new one should be created.

Heuristic (documented default, not specified precisely in the concept doc
- see README "Offene Punkte"):

1. If the sender (Contact) has recent correspondence already assigned to a
   Case, and the new mail's embedding is at least ``case_similarity_threshold``
   cosine-similar to the closest such mail, reuse that Case.
2. Otherwise, fall back to a tenant-wide semantic search across *all*
   contacts (not just this sender) within the lookback window, so a case
   that involves multiple people (e.g. several contacts at one customer)
   can still pick up a new participant. Same threshold applies.
3. Otherwise: no match: caller creates a new Case.

Both stages only consider mail from the last ``case_lookback_days`` days so
a mail isn't glued to a long-dead thread just because it happens to be
topically similar.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.case import Case
from app.models.email_message import EmailMessage


@dataclass
class CaseMatch:
    case: Case
    similarity: float


async def _best_match(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    embedding: list[float],
    cutoff: datetime,
    contact_id: uuid.UUID | None,
) -> tuple[uuid.UUID | None, float | None]:
    distance_expr = EmailMessage.embedding.cosine_distance(embedding)
    stmt = (
        select(EmailMessage.case_id, distance_expr.label("distance"))
        .where(
            EmailMessage.tenant_id == tenant_id,
            EmailMessage.case_id.isnot(None),
            EmailMessage.embedding.isnot(None),
            EmailMessage.received_at >= cutoff,
        )
        .order_by(distance_expr)
        # Only the nearest row is ever read (see .first() below); the index
        # scan can stop there.
        .limit(1)
    )
    if contact_id is not None:
        stmt = stmt.where(EmailMessage.contact_id == contact_id)

    row = (await db.execute(stmt)).first()
    if row is None:
        return None, None
    case_id, distance = row
    similarity = 1.0 - float(distance)
    return case_id, similarity


async def find_matching_case(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    contact_id: uuid.UUID | None,
    embedding: list[float],
) -> CaseMatch | None:
    settings = get_settings()
    cutoff = datetime.now(UTC) - timedelta(days=settings.case_lookback_days)

    # Stage 1: same-contact history.
    case_id, similarity = await _best_match(
        db, tenant_id=tenant_id, embedding=embedding, cutoff=cutoff, contact_id=contact_id
    )
    if case_id is not None and similarity is not None and similarity >= settings.case_similarity_threshold:
        case = await db.get(Case, case_id)
        if case is not None:
            return CaseMatch(case=case, similarity=similarity)

    # Stage 2: tenant-wide fallback (covers multi-contact cases).
    case_id, similarity = await _best_match(
        db, tenant_id=tenant_id, embedding=embedding, cutoff=cutoff, contact_id=None
    )
    if case_id is not None and similarity is not None and similarity >= settings.case_similarity_threshold:
        case = await db.get(Case, case_id)
        if case is not None:
            return CaseMatch(case=case, similarity=similarity)

    return None
