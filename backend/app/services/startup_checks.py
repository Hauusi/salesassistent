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
from urllib.parse import urlparse

from cryptography.fernet import Fernet
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


class StartupCheckFailed(RuntimeError):
    """A setting disagrees with the database it is pointed at, or a
    required setting is missing/invalid outright."""


# Substrings that show up in copy-pasted placeholder values far more often
# than in any real credential - a real Anthropic/Voyage key or Google
# client secret has never legitimately contained "changeme" or "your-".
_PLACEHOLDER_MARKERS = (
    "changeme",
    "change-me",
    "change_me",
    "placeholder",
    "your-",
    "your_",
    "xxx",
    "todo",
    "fixme",
    "example",
    "insert-your",
    "replace-me",
    "replace_me",
    "<",
    ">",
)

# Below this length, nothing on the list below is a real credential - the
# shortest genuine value among them (a Fernet key) is already 44 characters.
_MIN_CREDENTIAL_LENGTH = 16

_LOCALHOST_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1"})


def _looks_like_placeholder(value: str) -> bool:
    lowered = value.strip().lower()
    if len(lowered) < _MIN_CREDENTIAL_LENGTH:
        return True
    return any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


def _is_localhost_url(value: str) -> bool:
    return (urlparse(value).hostname or "").lower() in _LOCALHOST_HOSTS


def verify_required_settings(settings: Settings | None = None) -> None:
    """Fails fast if a setting the app cannot run without is missing, is
    still a placeholder value, or - for GOOGLE_REDIRECT_URI/FRONTEND_BASE_URL/
    API_BASE_URL outside local development - is still the code's built-in
    localhost fallback.

    Deliberately synchronous and DB-free: unlike the checks below, this one
    needs nothing but `Settings` itself, so it runs the same way in the API
    process (see app/main.py), the RQ worker, and the scheduler - all three
    would otherwise fail opaquely on whichever call happens to touch Gmail,
    Claude, Voyage or the encrypted token store first, potentially minutes
    or hours after boot instead of before the first request/job.

    Unlike verify_text_search_config/verify_embedding_dimensions, callers
    must NOT swallow this in development: a missing API key isn't "the
    database hasn't been migrated yet", it means nothing that needs it can
    ever work, in any environment.
    """
    settings = settings or get_settings()
    problems: list[str] = []

    # Secrets required in every environment, dev included - see
    # .env.example ("Copy to .env and fill in real values").
    required_secrets = {
        "GOOGLE_CLIENT_ID": settings.google_client_id,
        "GOOGLE_CLIENT_SECRET": settings.google_client_secret,
        "ANTHROPIC_API_KEY": settings.anthropic_api_key,
        "VOYAGE_API_KEY": settings.voyage_api_key,
    }
    for name, value in required_secrets.items():
        if not value.strip():
            problems.append(f"{name} ist nicht gesetzt.")
        elif _looks_like_placeholder(value):
            problems.append(f"{name} sieht wie ein Platzhalterwert aus: '{value}'.")

    # TOKEN_ENCRYPTION_KEY has a fully specified format - validating it
    # structurally catches a present-but-malformed key (every OAuth token
    # read/write raises identically to a missing one, but is easy to miss
    # by eye) rather than just checking it is non-empty.
    key = settings.token_encryption_key
    if not key.strip():
        problems.append("TOKEN_ENCRYPTION_KEY ist nicht gesetzt.")
    else:
        try:
            Fernet(key.encode())
        except Exception as exc:  # any failure here means "invalid key"
            problems.append(f"TOKEN_ENCRYPTION_KEY ist kein gültiger Fernet-Key: {exc}")

    # These three default to a localhost URL in code (see app/config.py) -
    # correct for local development (see .env.example), wrong the moment
    # this runs anywhere else. Gated on APP_ENV rather than checked
    # unconditionally so local development keeps working unmodified.
    if settings.app_env != "development":
        url_settings = {
            "GOOGLE_REDIRECT_URI": settings.google_redirect_uri,
            "FRONTEND_BASE_URL": settings.frontend_base_url,
            "API_BASE_URL": settings.api_base_url,
        }
        for name, value in url_settings.items():
            if not value.strip():
                problems.append(f"{name} ist nicht gesetzt.")
            elif _is_localhost_url(value):
                problems.append(
                    f"{name}='{value}' zeigt auf localhost, aber APP_ENV="
                    f"'{settings.app_env}' ist nicht 'development'. Vermutlich wurde "
                    "vergessen, diese Variable für dieses Environment zu setzen."
                )

    if problems:
        raise StartupCheckFailed(
            "Fehlende oder ungültige Pflicht-Konfiguration:\n- " + "\n- ".join(problems)
        )

    logger.info("startup_check_ok check=required_settings")


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


async def run_db_dependent_checks(engine: AsyncEngine) -> None:
    """Checks that need a live, migrated database - see app/main.py for why
    these (unlike verify_required_settings) get a development carve-out."""
    await verify_text_search_config(engine)
    await verify_embedding_dimensions(engine)
