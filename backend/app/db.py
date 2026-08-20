"""Async SQLAlchemy engine/session setup."""
from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

# One of only two places that read settings at import time, deliberately:
# the engine is process-global by nature and must exist before any module
# that imports it. Everywhere else calls get_settings() inside the function
# that needs it, so configuration is not frozen by import order.
settings = get_settings()

engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a request-scoped DB session."""
    async with async_session_factory() as session:
        yield session
