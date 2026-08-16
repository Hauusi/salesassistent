"""MVP single-tenant bootstrap.

The data model is multi-tenant from day one (every table carries
``tenant_id``), but this MVP is only exercised with a single tenant/user.
This helper gets-or-creates that default tenant and user so the rest of
the code never has to special-case "no tenant yet". Swapping this for real
signup/tenant-provisioning is a later-stage concern, not part of this
session's scope.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.tenant import Tenant
from app.models.user import User

settings = get_settings()


async def get_or_create_default_tenant(db: AsyncSession) -> Tenant:
    result = await db.execute(select(Tenant).where(Tenant.slug == settings.default_tenant_slug))
    tenant = result.scalar_one_or_none()
    if tenant is None:
        tenant = Tenant(name=settings.default_tenant_slug.capitalize(), slug=settings.default_tenant_slug)
        db.add(tenant)
        await db.flush()
    return tenant


async def get_or_create_user(db: AsyncSession, tenant: Tenant, email: str, name: str | None = None) -> User:
    result = await db.execute(
        select(User).where(User.tenant_id == tenant.id, User.email == email)
    )
    user = result.scalar_one_or_none()
    if user is None:
        user = User(tenant_id=tenant.id, email=email, name=name)
        db.add(user)
        await db.flush()
    return user
