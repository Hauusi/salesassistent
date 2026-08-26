from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    auth,
    cases,
    contacts,
    drafts,
    emails,
    knowledge,
    mailboxes,
    product_suggestions,
)
from app.config import get_settings
from app.db import engine
from app.logging_config import configure_logging
from app.services import startup_checks

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Validates settings that must agree with the database before serving.

    A mismatch here otherwise surfaces much later and far from its cause -
    as an insert that rejects every embedding, or a product search that
    silently matches nothing. Failing at boot puts it in front of whoever
    is deploying.
    """
    settings = get_settings()
    try:
        await startup_checks.run_all(engine)
    except startup_checks.StartupCheckFailed:
        # In development, surfacing this as a loud log and carrying on is
        # more useful than refusing to boot before the DB is migrated.
        if settings.app_env == "development":
            logger.exception("startup_check_failed (development: continuing anyway)")
        else:
            raise
    yield
    await engine.dispose()


app = FastAPI(title="Sales-Assistent - Mail-Modul", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(mailboxes.router)
app.include_router(drafts.router)
app.include_router(emails.router)
app.include_router(cases.router)
app.include_router(contacts.router)
app.include_router(knowledge.router)
app.include_router(product_suggestions.router)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}
