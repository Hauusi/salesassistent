"""Tests for the unattended poll job.

The poll job is the system's only entry point that nobody is watching. Its
failure behaviour is the contract under test here: a message that cannot
be processed must cost that message, never the mailbox.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.action_log import ActionLog
from app.models.email_message import EmailMessage
from app.models.enums import TypKategorie, WichtigkeitsKategorie
from app.models.mailbox import Mailbox
from app.models.tenant import Tenant
from app.models.user import User
from app.services.classification import ClassificationResult
from app.services.gmail_client import FetchedEmail
from app.workers import tasks


@pytest.fixture
async def mailbox(db_session: AsyncSession) -> Mailbox:
    tenant = Tenant(name="Test", slug=f"test-{uuid.uuid4().hex[:8]}")
    db_session.add(tenant)
    await db_session.flush()
    user = User(tenant_id=tenant.id, email="user@example.com")
    db_session.add(user)
    await db_session.flush()
    mailbox = Mailbox(
        tenant_id=tenant.id,
        user_id=user.id,
        email_address="me@example.com",
        last_synced_at=datetime.now(UTC) - timedelta(hours=1),
    )
    db_session.add(mailbox)
    # Commit rather than flush: _process_one_message rolls back on failure,
    # and conftest nests each test in a SAVEPOINT - an uncommitted fixture
    # would be unwound along with the failed message's work.
    await db_session.commit()
    return mailbox


def _fetched(message_id: str) -> FetchedEmail:
    return FetchedEmail(
        gmail_message_id=message_id,
        gmail_thread_id="thread-1",
        rfc822_message_id=f"<{message_id}@example.de>",
        subject="Betreff",
        sender_address="kunde@example.com",
        sender_name="Kunde",
        raw_content="Guten Tag, eine Frage.",
        snippet="Guten Tag",
        received_at=datetime.now(UTC),
    )


@pytest.fixture(autouse=True)
def _stub_llm(monkeypatch):
    from app.services import pipeline

    async def _classify(**_kwargs) -> ClassificationResult:
        return ClassificationResult(
            wichtigkeits_kategorie=WichtigkeitsKategorie.INFORMATION,
            typ=TypKategorie.KEINER,
            confidence=0.9,
            reasoning="Test",
        )

    async def _embed(*_args, **_kwargs) -> list[float]:
        return [0.1] * 1024

    monkeypatch.setattr(pipeline.classification, "classify_email", _classify)
    monkeypatch.setattr(pipeline.embeddings, "embed_text", _embed)


async def test_one_unprocessable_message_does_not_stop_the_batch(
    db_session: AsyncSession, mailbox: Mailbox
) -> None:
    """Regression: a raised exception aborted the loop, and because the
    watermark is only advanced afterwards, the next tick re-fetched the
    same batch and failed identically - forever."""
    result = tasks.PollResult()

    class Service:
        pass

    async def _get_message(_service, message_id: str) -> FetchedEmail:
        if message_id == "gm-bad":
            raise ValueError("kaputte Nachricht")
        return _fetched(message_id)

    import app.workers.tasks as tasks_module

    original = tasks_module.gmail_client.get_message
    tasks_module.gmail_client.get_message = _get_message
    try:
        for message_id in ["gm-1", "gm-bad", "gm-2"]:
            await tasks._process_one_message(
                db_session, service=Service(), mailbox=mailbox, message_id=message_id, result=result
            )
    finally:
        tasks_module.gmail_client.get_message = original

    assert result.processed == 2
    assert result.failed == 1

    stored = (
        await db_session.execute(
            select(EmailMessage.gmail_message_id).where(EmailMessage.mailbox_id == mailbox.id)
        )
    ).scalars().all()
    assert set(stored) == {"gm-1", "gm-2"}


async def test_a_failed_message_is_written_to_the_audit_trail(
    db_session: AsyncSession, mailbox: Mailbox
) -> None:
    """A skipped mail that leaves no trace is indistinguishable from a mail
    that never arrived."""
    result = tasks.PollResult()

    async def _boom(_service, _message_id):
        raise RuntimeError("Gmail kaputt")

    import app.workers.tasks as tasks_module

    original = tasks_module.gmail_client.get_message
    tasks_module.gmail_client.get_message = _boom
    try:
        await tasks._process_one_message(
            db_session, service=object(), mailbox=mailbox, message_id="gm-x", result=result
        )
    finally:
        tasks_module.gmail_client.get_message = original

    entries = (
        await db_session.execute(
            select(ActionLog).where(ActionLog.action == "mail_processing_failed")
        )
    ).scalars().all()

    assert len(entries) == 1
    assert entries[0].detail["gmail_message_id"] == "gm-x"
    assert entries[0].detail["error_type"] == "RuntimeError"
    assert "Gmail kaputt" in entries[0].detail["error"]


async def test_an_already_known_message_counts_as_known_not_processed(
    db_session: AsyncSession, mailbox: Mailbox
) -> None:
    result = tasks.PollResult()

    async def _get_message(_service, message_id: str) -> FetchedEmail:
        return _fetched(message_id)

    import app.workers.tasks as tasks_module

    original = tasks_module.gmail_client.get_message
    tasks_module.gmail_client.get_message = _get_message
    try:
        for _ in range(2):
            await tasks._process_one_message(
                db_session, service=object(), mailbox=mailbox, message_id="gm-dup", result=result
            )
    finally:
        tasks_module.gmail_client.get_message = original

    assert result.processed == 1
    assert result.already_known == 1
    assert result.failed == 0
