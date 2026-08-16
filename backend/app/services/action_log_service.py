"""Central helper to write ActionLog entries.

Every automated decision (classification, draft generation, filing) and
every human approval/rejection decision must go through this so the audit
trail is complete and consistently shaped. Does not commit - caller
controls the transaction boundary.
"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.action_log import ActionLog
from app.models.enums import ActionActor


async def log_action(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor: ActionActor,
    entity_type: str,
    entity_id: uuid.UUID,
    action: str,
    detail: dict | None = None,
    actor_user_id: uuid.UUID | None = None,
) -> ActionLog:
    entry = ActionLog(
        tenant_id=tenant_id,
        actor=actor,
        actor_user_id=actor_user_id,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        detail=detail or {},
    )
    db.add(entry)
    await db.flush()
    return entry
