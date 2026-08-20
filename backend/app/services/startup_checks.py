"""Configuration checks that run once at application startup.

Several settings must agree with something outside the process - a column
built by a migration, a configuration compiled into Postgres. Where they
disagree, the failure otherwise surfaces much later and far from its
cause: as an insert that rejects every embedding, or as a product search
that silently matches nothing.

These checks turn that into a clear error at boot, which is when someone
is actually looking.
"""
from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import get_settings

logger = logging.getLogger(__name__)


class StartupCheckFailed(RuntimeError):
    """A setting disagrees with the database it is pointed at."""


async def verify_text_search_config(engine: AsyncEngine) -> None:
    """PRODUCT_SEARCH_TEXT_CONFIG must name a configuration this server has.

    An unknown name makes every product search raise; a *valid but wrong*
    one (e.g. 'simple' against German text) silently stops stemming, which
    is the harder failure to spot.
    """
    configured = get_settings().product_search_text_config
    async with engine.connect() as conn:
        exists = await conn.scalar(
            text("SELECT 1 FROM pg_ts_config WHERE cfgname = :name"), {"name": configured}
        )
    if not exists:
        available = "SELECT cfgname FROM pg_ts_config ORDER BY cfgname"
        async with engine.connect() as conn:
            names = ", ".join((await conn.execute(text(available))).scalars().all())
        raise StartupCheckFailed(
            f"PRODUCT_SEARCH_TEXT_CONFIG='{configured}' ist keine bekannte "
            f"Text-Search-Konfiguration. Verfügbar: {names}"
        )
    logger.info("startup_check_ok check=text_search_config value=%s", configured)


async def verify_embedding_dimensions(engine: AsyncEngine) -> None:
    """EMBEDDING_DIMENSIONS must match the actual vector column width.

    The setting sizes the mapped column at import time while the migration
    wrote a fixed width. Changing the embedding model - to one with 512 or
    2048 dimensions - therefore desynced the two, and every insert failed
    at runtime with a dimension mismatch.
    """
    configured = get_settings().embedding_dimensions

    def _column_type(sync_conn) -> str | None:
        columns = inspect(sync_conn).get_columns("email_messages")
        for column in columns:
            if column["name"] == "embedding":
                return str(column["type"])
        return None

    async with engine.connect() as conn:
        rendered = await conn.run_sync(_column_type)

    if rendered is None:
        logger.warning("startup_check_skipped check=embedding_dimensions reason=column_not_found")
        return

    # Rendered as e.g. "VECTOR(1024)".
    digits = "".join(ch for ch in rendered if ch.isdigit())
    if digits and int(digits) != configured:
        raise StartupCheckFailed(
            f"EMBEDDING_DIMENSIONS={configured} passt nicht zur Spalte "
            f"email_messages.embedding ({rendered}). Entweder die Einstellung "
            f"zurücksetzen oder eine Migration schreiben, die die Spalte ändert."
        )
    logger.info("startup_check_ok check=embedding_dimensions value=%s", configured)


async def run_all(engine: AsyncEngine) -> None:
    await verify_text_search_config(engine)
    await verify_embedding_dimensions(engine)
