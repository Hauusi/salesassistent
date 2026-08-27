"""Tests for the mailbox poll-status fields the worker updates after every
poll attempt, success or crash - see app/workers/tasks.py and
app/models/mailbox.py. Previously a job-level crash was visible only in
container logs; a per-message failure (see app/workers/tasks.py::
_process_one_message) is deliberately NOT one of these - it costs that
message, not the mailbox's overall status.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db import async_session_factory
from app.models.action_log import ActionLog
from app.models.enums import MailboxPollStatus
from app.models.mailbox import Mailbox
from app.models.tenant import Tenant
from app.models.user import User
from app.services import gmail_client
from app.workers import tasks


class _FakeCredentials:
    token = None
    refresh_token = None
    expiry = None
    scopes = None


@pytest.fixture
async def committed_mailbox(_schema) -> str:
    """A mailbox actually committed to Postgres - _poll_mailbox_async opens
    its own session via async_session_factory(), which would not see an
    uncommitted row held only in a SAVEPOINT-scoped db_session (see
    tests/test_poll_mailbox_job_e2e.py for the same pattern)."""
    async with async_session_factory() as db:
        tenant = Tenant(name="Test", slug=f"test-{uuid.uuid4().hex[:8]}")
        db.add(tenant)
        await db.flush()
        user = User(tenant_id=tenant.id, email="user@example.com")
        db.add(user)
        await db.flush()
        mailbox = Mailbox(
            tenant_id=tenant.id,
            user_id=user.id,
            email_address="me@example.com",
            last_synced_at=datetime.now(UTC) - timedelta(hours=1),
        )
        db.add(mailbox)
        await db.commit()
        return str(mailbox.id)


async def _reload(mailbox_id: str) -> Mailbox:
    async with async_session_factory() as db:
        return await db.get(Mailbox, uuid.UUID(mailbox_id))


async def test_a_successful_poll_marks_the_mailbox_ok(
    committed_mailbox: str, monkeypatch
) -> None:
    async def _get_gmail_service(_mailbox):
        return object(), _FakeCredentials()

    async def _list_new_message_ids(_service, after_query=None, max_results=25):
        return []

    monkeypatch.setattr(gmail_client, "get_gmail_service", _get_gmail_service)
    monkeypatch.setattr(gmail_client, "list_new_message_ids", _list_new_message_ids)

    await tasks._poll_mailbox_async(committed_mailbox)

    mailbox = await _reload(committed_mailbox)
    assert mailbox.last_poll_status is MailboxPollStatus.OK
    assert mailbox.last_poll_error_message is None
    assert mailbox.last_poll_at is not None


async def test_a_gmail_auth_failure_marks_the_mailbox_error_and_still_raises(
    committed_mailbox: str, monkeypatch
) -> None:
    """The status write must not swallow the failure - RQ's own retry
    policy (see app/workers/queue.py) still needs to see the job fail."""

    async def _boom(_mailbox):
        raise RuntimeError("invalid_grant: token has been revoked")

    monkeypatch.setattr(gmail_client, "get_gmail_service", _boom)

    with pytest.raises(RuntimeError, match="invalid_grant"):
        await tasks._poll_mailbox_async(committed_mailbox)

    mailbox = await _reload(committed_mailbox)
    assert mailbox.last_poll_status is MailboxPollStatus.ERROR
    assert "invalid_grant" in mailbox.last_poll_error_message
    assert mailbox.last_poll_at is not None


async def test_a_failure_listing_messages_marks_the_mailbox_error(
    committed_mailbox: str, monkeypatch
) -> None:
    async def _get_gmail_service(_mailbox):
        return object(), _FakeCredentials()

    async def _boom(_service, after_query=None, max_results=25):
        raise RuntimeError("rate limit exceeded")

    monkeypatch.setattr(gmail_client, "get_gmail_service", _get_gmail_service)
    monkeypatch.setattr(gmail_client, "list_new_message_ids", _boom)

    with pytest.raises(RuntimeError):
        await tasks._poll_mailbox_async(committed_mailbox)

    mailbox = await _reload(committed_mailbox)
    assert mailbox.last_poll_status is MailboxPollStatus.ERROR
    assert "rate limit" in mailbox.last_poll_error_message


async def test_a_stale_error_clears_on_the_next_successful_poll(
    committed_mailbox: str, monkeypatch
) -> None:
    """A recovered mailbox must stop showing yesterday's error message."""

    async def _boom(_mailbox):
        raise RuntimeError("temporary outage")

    monkeypatch.setattr(gmail_client, "get_gmail_service", _boom)
    with pytest.raises(RuntimeError):
        await tasks._poll_mailbox_async(committed_mailbox)
    assert (await _reload(committed_mailbox)).last_poll_status is MailboxPollStatus.ERROR

    async def _get_gmail_service(_mailbox):
        return object(), _FakeCredentials()

    async def _list_new_message_ids(_service, after_query=None, max_results=25):
        return []

    monkeypatch.setattr(gmail_client, "get_gmail_service", _get_gmail_service)
    monkeypatch.setattr(gmail_client, "list_new_message_ids", _list_new_message_ids)

    await tasks._poll_mailbox_async(committed_mailbox)

    mailbox = await _reload(committed_mailbox)
    assert mailbox.last_poll_status is MailboxPollStatus.OK
    assert mailbox.last_poll_error_message is None


async def test_a_per_message_failure_does_not_flip_the_mailbox_to_error(
    committed_mailbox: str, monkeypatch
) -> None:
    """One bad mail costs that mail (see _process_one_message) - it must
    not also make the mailbox's overall poll status look broken."""

    async def _get_gmail_service(_mailbox):
        return object(), _FakeCredentials()

    async def _list_new_message_ids(_service, after_query=None, max_results=25):
        return ["gm-bad"]

    async def _get_message(_service, _message_id):
        raise ValueError("kaputte Nachricht")

    monkeypatch.setattr(gmail_client, "get_gmail_service", _get_gmail_service)
    monkeypatch.setattr(gmail_client, "list_new_message_ids", _list_new_message_ids)
    monkeypatch.setattr(gmail_client, "get_message", _get_message)

    result = await tasks._poll_mailbox_async(committed_mailbox)
    assert result.failed == 1

    mailbox = await _reload(committed_mailbox)
    assert mailbox.last_poll_status is MailboxPollStatus.OK
    assert mailbox.last_poll_error_message is None


async def test_two_consecutive_message_failures_each_write_their_own_audit_entry(
    committed_mailbox: str, monkeypatch
) -> None:
    """Regression: db.rollback() after a per-message failure expires every
    attribute on the shared `mailbox` object that the caller's loop reuses
    for the next message_id. Without an explicit, async-safe refresh
    before reuse, the first failure's own audit-trail write crashed with
    MissingGreenlet trying to read mailbox.tenant_id/.id, and the second
    message inherited the same poisoned object and crashed identically
    before it ever reached Gmail - only one message's failure would ever
    make it into the audit trail, no matter how many followed it.

    This only reproduces on a real (non-SAVEPOINT) session - see
    tests/test_worker_tasks.py for the SAVEPOINT-scoped versions of this
    same scenario, which do not expose it (a SAVEPOINT-level rollback
    does not expire attributes the way a real rollback does)."""

    async def _get_gmail_service(_mailbox):
        return object(), _FakeCredentials()

    async def _list_new_message_ids(_service, after_query=None, max_results=25):
        return ["gm-bad-1", "gm-bad-2"]

    async def _get_message(_service, _message_id):
        raise ValueError("kaputte Nachricht")

    monkeypatch.setattr(gmail_client, "get_gmail_service", _get_gmail_service)
    monkeypatch.setattr(gmail_client, "list_new_message_ids", _list_new_message_ids)
    monkeypatch.setattr(gmail_client, "get_message", _get_message)

    result = await tasks._poll_mailbox_async(committed_mailbox)
    assert result.failed == 2

    async with async_session_factory() as db:
        entries = (
            await db.execute(
                select(ActionLog.detail).where(ActionLog.action == "mail_processing_failed")
            )
        ).scalars().all()
    assert {e["gmail_message_id"] for e in entries} == {"gm-bad-1", "gm-bad-2"}

    mailbox = await _reload(committed_mailbox)
    assert mailbox.last_poll_status is MailboxPollStatus.OK
