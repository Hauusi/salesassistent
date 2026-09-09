"""Tests for the approval workflow - the one irreversible action.

Once Gmail accepts a message, no local state can un-send it, so these
cover the paths that decide "sent zero times", "sent once" and, above all,
never "sent twice".
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.action_log import ActionLog
from app.models.case import Case
from app.models.draft import Draft
from app.models.email_message import EmailMessage
from app.models.enums import DealStage, DraftStatus, EmailStatus, TypKategorie, WichtigkeitsKategorie
from app.models.mailbox import Mailbox
from app.models.tenant import Tenant


@pytest.fixture
async def draft(db_session: AsyncSession, tenant: Tenant) -> Draft:
    user_id = (
        await db_session.execute(select(Mailbox.user_id).limit(1))
    ).scalar_one_or_none()
    if user_id is None:
        from app.models.user import User

        user_id = (await db_session.execute(select(User.id).limit(1))).scalar_one()

    mailbox = Mailbox(
        tenant_id=tenant.id, user_id=user_id, email_address="me@example.com", is_active=True
    )
    db_session.add(mailbox)
    await db_session.flush()

    email = EmailMessage(
        tenant_id=tenant.id,
        mailbox_id=mailbox.id,
        gmail_message_id=f"gm-{uuid.uuid4().hex[:8]}",
        gmail_thread_id="thread-1",
        rfc822_message_id="<abc@kunde.de>",
        subject="Angebotsanfrage",
        sender_address="kunde@example.com",
        sender_name="Kunde",
        raw_content="Bitte um ein Angebot.",
        snippet="Bitte um ein Angebot.",
        wichtigkeits_kategorie=WichtigkeitsKategorie.ANTWORT_ERFORDERLICH,
        typ=TypKategorie.ANFRAGE,
        status=EmailStatus.WARTET_AUF_FREIGABE,
        received_at=datetime.now(UTC),
    )
    db_session.add(email)
    await db_session.flush()

    draft = Draft(
        tenant_id=tenant.id,
        email_message_id=email.id,
        subject="Re: Angebotsanfrage",
        body="Gerne unterbreiten wir Ihnen ein Angebot.",
        status=DraftStatus.ENTWURF,
    )
    db_session.add(draft)
    await db_session.commit()
    return draft


@pytest.fixture
async def draft_with_case(db_session: AsyncSession, tenant: Tenant, draft: Draft) -> Draft:
    """Same as `draft`, but its source email belongs to a Case - the
    precondition for the ANFRAGE -> ANGEBOT_ERSTELLT transition on approve
    (see app/services/case_stage_service.py)."""
    case = Case(tenant_id=tenant.id, title="Testcase")
    db_session.add(case)
    await db_session.flush()

    email = (
        await db_session.execute(
            select(EmailMessage).where(EmailMessage.id == draft.email_message_id)
        )
    ).scalar_one()
    email.case_id = case.id
    await db_session.commit()
    return draft


@pytest.fixture
def sent_messages(monkeypatch) -> list[dict]:
    """Records every Gmail send instead of performing one."""
    sent: list[dict] = []

    from app.services import gmail_client

    async def _get_service(_mailbox):
        return object(), _Creds()

    async def _send_reply(_service, **kwargs) -> str:
        sent.append(kwargs)
        return f"sent-{len(sent)}"

    monkeypatch.setattr(gmail_client, "get_gmail_service", _get_service)
    monkeypatch.setattr(gmail_client, "send_reply", _send_reply)
    monkeypatch.setattr(gmail_client, "apply_refreshed_credentials", lambda _m, _c: False)
    return sent


class _Creds:
    token = None
    refresh_token = None
    expiry = None
    scopes = None


async def test_approve_sends_the_draft_and_marks_it_versendet(
    api_client, db_session: AsyncSession, draft: Draft, sent_messages: list[dict]
) -> None:
    response = await api_client.post(f"/api/drafts/{draft.id}/approve")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "versendet"
    assert len(sent_messages) == 1
    assert sent_messages[0]["to_address"] == "kunde@example.com"
    assert sent_messages[0]["in_reply_to_rfc822_id"] == "<abc@kunde.de>"

    await db_session.refresh(draft)
    assert draft.status is DraftStatus.VERSENDET
    assert draft.gmail_sent_message_id == "sent-1"
    assert draft.sent_at is not None


async def test_approve_marks_the_source_mail_as_done(
    api_client, db_session: AsyncSession, draft: Draft, sent_messages: list[dict]
) -> None:
    await api_client.post(f"/api/drafts/{draft.id}/approve")

    email = (
        await db_session.execute(
            select(EmailMessage).where(EmailMessage.id == draft.email_message_id)
        )
    ).scalar_one()
    assert email.status is EmailStatus.ERLEDIGT


async def test_approve_moves_the_case_from_anfrage_to_angebot_erstellt(
    api_client, db_session: AsyncSession, draft_with_case: Draft, sent_messages: list[dict]
) -> None:
    email = (
        await db_session.execute(
            select(EmailMessage).where(EmailMessage.id == draft_with_case.email_message_id)
        )
    ).scalar_one()
    case = (await db_session.execute(select(Case).where(Case.id == email.case_id))).scalar_one()
    assert case.deal_stage is DealStage.ANFRAGE  # sanity check on the default

    response = await api_client.post(f"/api/drafts/{draft_with_case.id}/approve")

    assert response.status_code == 200, response.text
    await db_session.refresh(case)
    assert case.deal_stage is DealStage.ANGEBOT_ERSTELLT

    actions = (
        await db_session.execute(select(ActionLog.action).where(ActionLog.entity_id == case.id))
    ).scalars().all()
    assert "deal_stage_changed" in actions


async def test_a_second_approval_is_rejected_and_sends_nothing(
    api_client, draft: Draft, sent_messages: list[dict]
) -> None:
    """Regression: both requests passed the status check and the customer
    received the reply twice."""
    first = await api_client.post(f"/api/drafts/{draft.id}/approve")
    second = await api_client.post(f"/api/drafts/{draft.id}/approve")

    assert first.status_code == 200
    assert second.status_code == 409
    assert len(sent_messages) == 1


async def test_a_failed_send_releases_the_claim_so_the_user_can_retry(
    api_client, db_session: AsyncSession, draft: Draft, monkeypatch
) -> None:
    from app.services import gmail_client

    async def _get_service(_mailbox):
        return object(), _Creds()

    async def _boom(_service, **_kwargs):
        raise RuntimeError("Gmail send failed: quota exceeded")

    monkeypatch.setattr(gmail_client, "get_gmail_service", _get_service)
    monkeypatch.setattr(gmail_client, "send_reply", _boom)
    monkeypatch.setattr(gmail_client, "apply_refreshed_credentials", lambda _m, _c: False)

    response = await api_client.post(f"/api/drafts/{draft.id}/approve")

    assert response.status_code == 502
    await db_session.refresh(draft)
    assert draft.status is DraftStatus.ENTWURF, "a failed send must not leave the draft claimed"
    assert draft.approved_at is None
    assert draft.sent_at is None


async def test_a_failed_send_is_written_to_the_audit_trail(
    api_client, db_session: AsyncSession, draft: Draft, monkeypatch
) -> None:
    from app.services import gmail_client

    async def _get_service(_mailbox):
        return object(), _Creds()

    async def _boom(_service, **_kwargs):
        raise RuntimeError("Gmail kaputt")

    monkeypatch.setattr(gmail_client, "get_gmail_service", _get_service)
    monkeypatch.setattr(gmail_client, "send_reply", _boom)
    monkeypatch.setattr(gmail_client, "apply_refreshed_credentials", lambda _m, _c: False)

    await api_client.post(f"/api/drafts/{draft.id}/approve")

    actions = (
        await db_session.execute(select(ActionLog.action).where(ActionLog.entity_id == draft.id))
    ).scalars().all()
    assert "draft_send_failed" in actions


async def test_approving_an_already_rejected_draft_is_a_conflict(
    api_client, db_session: AsyncSession, draft: Draft, sent_messages: list[dict]
) -> None:
    draft.status = DraftStatus.ABGELEHNT
    await db_session.commit()

    response = await api_client.post(f"/api/drafts/{draft.id}/approve")

    assert response.status_code == 409
    assert sent_messages == []


async def test_approving_an_unknown_draft_is_a_404(api_client, tenant: Tenant) -> None:
    response = await api_client.post(f"/api/drafts/{uuid.uuid4()}/approve")
    assert response.status_code == 404


async def test_approving_without_a_connected_mailbox_is_a_conflict(api_client) -> None:
    """No tenant user yet means no mailbox has ever been connected - the
    dependency says so before the handler runs."""
    response = await api_client.post(f"/api/drafts/{uuid.uuid4()}/approve")
    assert response.status_code == 409


async def test_reject_marks_the_draft_and_records_the_reason(
    api_client, db_session: AsyncSession, draft: Draft
) -> None:
    response = await api_client.post(
        f"/api/drafts/{draft.id}/reject", json={"reason": "Preis stimmt nicht"}
    )

    assert response.status_code == 200
    await db_session.refresh(draft)
    assert draft.status is DraftStatus.ABGELEHNT
    assert draft.rejected_reason == "Preis stimmt nicht"


async def test_update_changes_subject_and_body(
    api_client, db_session: AsyncSession, draft: Draft
) -> None:
    response = await api_client.put(
        f"/api/drafts/{draft.id}", json={"subject": "Neuer Betreff", "body": "Neuer Text"}
    )

    assert response.status_code == 200
    await db_session.refresh(draft)
    assert draft.subject == "Neuer Betreff"
    assert draft.body == "Neuer Text"


async def test_a_sent_draft_can_no_longer_be_edited(
    api_client, db_session: AsyncSession, draft: Draft, sent_messages: list[dict]
) -> None:
    await api_client.post(f"/api/drafts/{draft.id}/approve")

    response = await api_client.put(
        f"/api/drafts/{draft.id}", json={"subject": "Zu spät", "body": "Zu spät"}
    )
    assert response.status_code == 409


async def test_list_drafts_returns_only_open_ones_by_default(
    api_client, draft: Draft, sent_messages: list[dict]
) -> None:
    before = await api_client.get("/api/drafts")
    assert [d["id"] for d in before.json()] == [str(draft.id)]

    await api_client.post(f"/api/drafts/{draft.id}/approve")

    after = await api_client.get("/api/drafts")
    assert after.json() == []
