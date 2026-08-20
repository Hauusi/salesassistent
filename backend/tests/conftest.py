"""Test configuration.

Points the app at a dedicated `salesassistent_test` Postgres database so
DB-backed tests (case matching, pipeline) exercise real pgvector
cosine-distance queries instead of a mock. The database itself must exist
(`createdb salesassistent_test` - see README "Tests"), but the `_schema`
fixture below enables the pgvector extension on it itself, so no manual
`CREATE EXTENSION` step is needed. Env vars are set before any `app.*`
module is imported, since several modules read settings once at import
time.
"""
from __future__ import annotations

import os

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/salesassistent_test"
)
os.environ.setdefault("TOKEN_ENCRYPTION_KEY", "-Fh2wq9s0V1z7QpM3Yv8Jk6XoN4Rr5Td2Ac1Bw0EeGs=")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client-id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-client-secret")

# Assigned, not setdefault: Settings also reads backend/.env, so a
# developer's real key would otherwise reach the test process. A test that
# slips past its mocks then bills a real account and sends real traffic -
# which has happened here once, caught only because the key was fake. With
# a placeholder the worst case is a fast 401.
os.environ["ANTHROPIC_API_KEY"] = "test-anthropic-key-not-a-real-key"
os.environ["VOYAGE_API_KEY"] = "test-voyage-key-not-a-real-key"

import importlib
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.models  # noqa: F401 - registers all models on Base.metadata
from app.db import Base, engine


@pytest_asyncio.fixture
async def _schema() -> AsyncIterator[None]:
    # asyncpg connections are bound to the event loop they were created on,
    # but pytest-asyncio gives each test function its own loop - dispose the
    # pool first so this test starts with connections on its own loop.
    await engine.dispose()
    async with engine.begin() as conn:
        # EmailMessage.embedding is a pgvector column (see
        # app/models/email_message.py) - create_all() fails with
        # "type vector does not exist" unless this extension is enabled on
        # this database first. IF NOT EXISTS makes this idempotent, so a
        # fresh `createdb salesassistent_test` is the only manual setup
        # step left (see README "Tests").
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(_schema) -> AsyncIterator[AsyncSession]:
    """One AsyncSession per test; all changes are rolled back afterwards so
    tests stay isolated without needing per-test create/drop of tables."""
    connection = await engine.connect()
    transaction = await connection.begin()
    session_factory = async_sessionmaker(bind=connection, expire_on_commit=False, class_=AsyncSession)
    session = session_factory()

    # Commits inside application code (e.g. pipeline.process_incoming_email)
    # would normally end the outer transaction - nest in a SAVEPOINT instead
    # so the whole test still rolls back at the end.
    await connection.begin_nested()

    def _restart_savepoint(sess, trans):
        if trans.nested and not trans._parent.nested:
            sess.begin_nested()

    from sqlalchemy import event

    event.listen(session.sync_session, "after_transaction_end", _restart_savepoint)

    try:
        yield session
    finally:
        event.remove(session.sync_session, "after_transaction_end", _restart_savepoint)
        await session.close()
        await transaction.rollback()
        await connection.close()


@pytest_asyncio.fixture
async def api_client(db_session: AsyncSession):
    """An httpx client wired to the ASGI app, sharing the test's session.

    The app's get_db dependency is overridden so route handlers run inside
    the same transaction the test does - route commits become SAVEPOINT
    releases (see db_session above) and everything still rolls back.
    """
    import httpx

    from app.db import get_db
    from app.main import app

    async def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def tenant(db_session: AsyncSession):
    """The default tenant the API's deps resolve to, plus its user."""
    from app.config import get_settings
    from app.models.tenant import Tenant
    from app.models.user import User

    tenant = Tenant(name="Test", slug=get_settings().default_tenant_slug)
    db_session.add(tenant)
    await db_session.flush()
    db_session.add(User(tenant_id=tenant.id, email="user@example.com", name="Test User"))
    await db_session.commit()
    return tenant


@pytest.fixture(autouse=True)
def _no_outbound_model_calls(request, monkeypatch):
    """Fails a test that tries to build a real Anthropic or Voyage client.

    The mocks in this suite are per-test, and one that forgets to patch a
    boundary would otherwise silently make a live API call - slow, billable
    and, for a send path, potentially visible to a real customer. This turns
    a missed mock into an immediate, obvious failure.

    Tests that deliberately construct a client (the llm_client unit tests
    pass a fake in directly) are unaffected; they never call the factories.
    """
    from app.services import llm_client

    def _refuse(*_args, **_kwargs):
        raise AssertionError(
            "Ein Test wollte einen echten Model-Client bauen. Boundary mocken "
            "(siehe tests/test_end_to_end.py) statt live zu telefonieren."
        )

    monkeypatch.setattr(llm_client, "get_anthropic_client", _refuse)
    monkeypatch.setattr(llm_client, "get_voyage_client", _refuse)
    for module_name in ("app.services.classification", "app.services.draft_generation"):
        module = importlib.import_module(module_name)
        if hasattr(module, "get_anthropic_client"):
            monkeypatch.setattr(module, "get_anthropic_client", _refuse)
    monkeypatch.setattr(
        importlib.import_module("app.services.embeddings"), "get_voyage_client", _refuse
    )
