"""Shared FastAPI dependencies.

MVP has no login/session system (out of scope for this module) - the
single default tenant and its (single, MVP-scope) user are resolved on
every request. Swapping this for real multi-user auth is a later-stage
concern; see README "Offene Punkte".
"""
from __future__ import annotations

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.tenant import Tenant
from app.models.user import User
from app.services.tenant_bootstrap import get_or_create_default_tenant


async def get_current_tenant(db: AsyncSession = Depends(get_db)) -> Tenant:
    return await get_or_create_default_tenant(db)


async def get_current_user(
    db: AsyncSession = Depends(get_db), tenant: Tenant = Depends(get_current_tenant)
) -> User:
    result = await db.execute(
        select(User).where(User.tenant_id == tenant.id).order_by(User.created_at).limit(1)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=409,
            detail="Kein Postfach verbunden. Bitte zuerst /api/auth/gmail/connect durchlaufen.",
        )
    return user
