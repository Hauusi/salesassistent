"""Tests for the inbox listing filters.

spam_verdacht is hidden from the default view but never deleted (concept
5.3). Getting that gate wrong is invisible in the happy path and makes
whole categories unreachable in the UI.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email_message import EmailMessage
from app.models.enums import EmailStatus, TypKategorie, WichtigkeitsKategorie
from app.models.mailbox import Mailbox
from app.models.tenant import Tenant
from app.models.user import User


@pytest.fixture
async def inbox(db_session: AsyncSession, tenant: Tenant) -> Mailbox:
    user = (await db_session.execute(__import__("sqlalchemy").select(User).limit(1))).scalar_one()
    mailbox = Mailbox(tenant_id=tenant.id, user_id=user.id, email_address="me@example.com")
    db_session.add(mailbox)
    await db_session.flush()

    now = datetime.now(timezone.utc)
    rows = [
        ("Anfrage", WichtigkeitsKategorie.ANTWORT_ERFORDERLICH, TypKategorie.ANFRAGE, EmailStatus.WARTET_AUF_FREIGABE),
        ("Infomail", WichtigkeitsKategorie.INFORMATION, TypKategorie.KEINER, EmailStatus.ABGELEGT),
        ("Newsletter", WichtigkeitsKategorie.NEWSLETTER, TypKategorie.KEINER, EmailStatus.ERLEDIGT),
        ("Phishing", WichtigkeitsKategorie.SPAM_VERDACHT, TypKategorie.KEINER, EmailStatus.AUSGEBLENDET),
    ]
    for index, (subject, wichtigkeit, typ, status) in enumerate(rows):
        db_session.add(
            EmailMessage(
                tenant_id=tenant.id,
                mailbox_id=mailbox.id,
                gmail_message_id=f"gm-{uuid.uuid4().hex[:8]}",
                subject=subject,
                sender_address="absender@example.com",
                raw_content="Inhalt",
                snippet="Inhalt",
                wichtigkeits_kategorie=wichtigkeit,
                typ=typ,
                status=status,
                received_at=now - timedelta(minutes=index),
            )
        )
    await db_session.commit()
    return mailbox


async def _subjects(api_client, query: str = "") -> list[str]:
    response = await api_client.get(f"/api/emails{query}")
    assert response.status_code == 200, response.text
    return [row["subject"] for row in response.json()]


async def test_default_view_hides_spam_but_shows_everything_else(api_client, inbox) -> None:
    assert await _subjects(api_client) == ["Anfrage", "Infomail", "Newsletter"]


async def test_include_spam_shows_it(api_client, inbox) -> None:
    assert "Phishing" in await _subjects(api_client, "?include_spam=true")


async def test_filtering_for_the_spam_category_returns_it(api_client, inbox) -> None:
    """Regression: this returned an empty list unless include_spam was also
    passed, because the spam gate was coupled to `status is None`. In the
    UI that meant picking "Spam-Verdacht" from the dropdown showed nothing."""
    assert await _subjects(api_client, "?wichtigkeit=spam_verdacht") == ["Phishing"]


async def test_a_status_filter_does_not_silently_unhide_spam(api_client, inbox) -> None:
    """Regression: any status filter disabled spam hiding altogether."""
    assert await _subjects(api_client, "?status=ausgeblendet") == []


async def test_status_filter_still_works_for_normal_categories(api_client, inbox) -> None:
    assert await _subjects(api_client, "?status=abgelegt") == ["Infomail"]


async def test_filtering_by_category(api_client, inbox) -> None:
    assert await _subjects(api_client, "?wichtigkeit=newsletter") == ["Newsletter"]


async def test_filtering_by_typ(api_client, inbox) -> None:
    assert await _subjects(api_client, "?typ=anfrage") == ["Anfrage"]


async def test_results_are_newest_first(api_client, inbox) -> None:
    assert await _subjects(api_client) == ["Anfrage", "Infomail", "Newsletter"]


async def test_unknown_email_is_a_404(api_client, tenant) -> None:
    response = await api_client.get(f"/api/emails/{uuid.uuid4()}")
    assert response.status_code == 404
