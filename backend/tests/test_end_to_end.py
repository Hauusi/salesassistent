"""One mail, all the way through, against a real database.

Every other test in this suite exercises one component with its neighbours
mocked. This one fakes *only* the two external boundaries - the Gmail API
and the model providers - and lets everything between them run for real:
the RQ job body, the pipeline, classification, embedding, case matching,
product grounding, draft generation, the HTTP API, and the approval that
sends.

It also runs without the transactional `db_session` fixture on purpose.
The worker opens its own session via async_session_factory and commits per
message; wrapping that in an outer transaction would test a code path
production never takes. Tables come from `_schema` and are dropped after.
"""
from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.db import async_session_factory
from app.models.action_log import ActionLog
from app.models.case import Case
from app.models.draft import Draft
from app.models.email_message import EmailMessage
from app.models.enums import DraftStatus, EmailStatus, TypKategorie, WichtigkeitsKategorie
from app.models.mailbox import Mailbox
from app.models.product import Product
from app.models.tenant import Tenant
from app.models.user import User
from app.services import (
    classification,
    draft_generation,
    embeddings,
    gmail_client,
    llm_client,
)
from app.workers import tasks
from tests.mocks import FakeAnthropicClient, tool_response

# --- fakes at the two external boundaries only -------------------------


class _FakeVoyageClient:
    """Deterministic embeddings: same text in, same vector out, and two
    different texts land far enough apart for case matching to tell them
    apart."""

    def embed(self, texts, model, input_type):
        return SimpleNamespace(embeddings=[_vector_for(texts[0])])


def _vector_for(text: str) -> list[float]:
    lowered = text.lower()
    # A crude topical signature: enough for cosine similarity to group two
    # mails about the same subject and separate an unrelated one.
    topic = 1.0 if "aluminiumprofil" in lowered else 0.0
    other = 1.0 if "rechnung" in lowered else 0.0
    vector = [0.01] * 1024
    vector[0] = topic
    vector[1] = other
    return vector


class _FakeGmailService:
    """Stands in for the googleapiclient resource, recording what was sent."""

    def __init__(self, messages: dict[str, dict]):
        self.messages_by_id = messages
        self.sent: list[dict] = []


def _raw_message(
    *, message_id: str, subject: str, sender: str, body: str, list_unsubscribe: str | None = None
) -> dict:
    headers = [
        {"name": "From", "value": sender},
        {"name": "Subject", "value": subject},
        {"name": "Date", "value": "Mon, 12 Aug 2026 10:15:00 +0200"},
        {"name": "Message-ID", "value": f"<{message_id}@kunde.example>"},
    ]
    if list_unsubscribe:
        headers.append({"name": "List-Unsubscribe", "value": list_unsubscribe})
    return {
        "id": message_id,
        "threadId": f"thread-{message_id}",
        "snippet": body[:100],
        "payload": {
            "mimeType": "text/plain",
            "headers": headers,
            "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()},
        },
    }


def _classification_for(user_message: str) -> dict:
    """A stand-in classifier keyed on the mail text, so the fixtures below
    read as the categories they are meant to represent."""
    lowered = user_message.lower()
    if "angebot" in lowered or "aluminiumprofil" in lowered:
        return {
            "wichtigkeits_kategorie": "antwort_erforderlich",
            "typ": "anfrage",
            "confidence": 0.94,
            "reasoning": "Kunde fragt nach Angebot",
            "suggested_case_title": "Angebotsanfrage Aluminiumprofile",
        }
    if "rechnung" in lowered:
        return {
            "wichtigkeits_kategorie": "information",
            "typ": "keiner",
            "confidence": 0.88,
            "reasoning": "Reine Mitteilung",
        }
    return {
        "wichtigkeits_kategorie": "information",
        "typ": "keiner",
        "confidence": 0.7,
        "reasoning": "Sonstiges",
    }


@pytest.fixture
def fake_providers(monkeypatch):
    """Replaces Anthropic and Voyage at the SDK boundary.

    Deliberately patches the client factories rather than the service
    functions, so classification.py and draft_generation.py - prompt
    construction, forced tool use, retry, response coercion - all run for
    real.
    """
    calls: dict[str, int] = {"classify": 0, "draft": 0}

    def respond(kwargs):
        tool_name = kwargs["tool_choice"]["name"]
        user_message = kwargs["messages"][0]["content"]
        if tool_name == "classify_email":
            calls["classify"] += 1
            return tool_response(**_classification_for(user_message))
        calls["draft"] += 1
        # Echo part of the prompt back, so the test can prove the product
        # grounding actually reached the model.
        mentions_product = "Aluminiumprofil 40x40" in user_message
        return tool_response(
            subject="Re: Angebotsanfrage Aluminiumprofile",
            body=(
                "Guten Tag,\n\ngerne unterbreiten wir Ihnen ein Angebot."
                + (
                    "\nAluminiumprofil 40x40, 12.50 EUR, lieferbar in 3-5 Werktagen."
                    if mentions_product
                    else ""
                )
                + "\n\nMit freundlichen Grüßen"
            ),
        )

    # Patched where the name is *used*, not where it is defined: both
    # services do `from ... import get_anthropic_client`, so they hold
    # their own reference and patching llm_client alone has no effect.
    # (Getting this wrong once sent a live request to api.anthropic.com -
    # hence the guard in conftest.)
    def _anthropic():
        return FakeAnthropicClient(response_fn=respond)

    monkeypatch.setattr(classification, "get_anthropic_client", _anthropic)
    monkeypatch.setattr(draft_generation, "get_anthropic_client", _anthropic)
    monkeypatch.setattr(llm_client, "get_anthropic_client", _anthropic)
    monkeypatch.setattr(embeddings, "get_voyage_client", _FakeVoyageClient)
    monkeypatch.setattr(llm_client, "get_voyage_client", _FakeVoyageClient)
    return calls


@pytest.fixture
def fake_gmail(monkeypatch):
    """Replaces the Gmail transport, recording sends."""
    state: dict[str, object] = {"messages": {}, "sent": []}

    async def _get_service(_mailbox):
        class _Creds:
            token = None
            refresh_token = None
            expiry = None
            scopes = None

        return _FakeGmailService(state["messages"]), _Creds()

    async def _list_new_message_ids(_service, after_query=None, max_results=25):
        return list(state["messages"].keys())[:max_results]

    async def _get_message(_service, message_id):
        return gmail_client.parse_gmail_message(state["messages"][message_id])

    async def _send_reply(_service, **kwargs):
        state["sent"].append(kwargs)
        return f"gmail-sent-{len(state['sent'])}"

    monkeypatch.setattr(gmail_client, "get_gmail_service", _get_service)
    monkeypatch.setattr(gmail_client, "list_new_message_ids", _list_new_message_ids)
    monkeypatch.setattr(gmail_client, "get_message", _get_message)
    monkeypatch.setattr(gmail_client, "send_reply", _send_reply)
    monkeypatch.setattr(gmail_client, "apply_refreshed_credentials", lambda _m, _c: False)
    return state


@pytest_asyncio.fixture
async def seeded(_schema):
    """A tenant, user, mailbox and one catalog product - committed for
    real, since the worker uses its own session."""
    async with async_session_factory() as db:
        tenant = Tenant(name="E2E", slug="default")
        db.add(tenant)
        await db.flush()
        user = User(tenant_id=tenant.id, email="vertrieb@firma.example", name="Vertrieb")
        db.add(user)
        await db.flush()
        mailbox = Mailbox(
            tenant_id=tenant.id,
            user_id=user.id,
            email_address="vertrieb@firma.example",
            is_active=True,
            last_synced_at=datetime.now(UTC) - timedelta(days=1),
        )
        db.add(mailbox)
        db.add(
            Product(
                tenant_id=tenant.id,
                name="Aluminiumprofil 40x40",
                category="Profile",
                sku="ALU-4040",
                price=12.50,
                currency="EUR",
                availability="3-5 Werktage",
                description="Strangpressprofil, eloxiert",
                specs={"Gewicht": "1.6 kg/m"},
            )
        )
        await db.commit()
        return {"tenant_id": tenant.id, "mailbox_id": mailbox.id, "user_id": user.id}


@pytest_asyncio.fixture
async def client():
    """Talks to the real app with the real get_db - no override, so the
    API commits for real like it does in production."""
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_inquiry_becomes_an_approved_reply(
    seeded, fake_providers, fake_gmail, client
) -> None:
    """The whole path: poll -> classify -> case -> product grounding ->
    draft -> API -> approve -> send."""
    fake_gmail["messages"] = {
        "gm-anfrage": _raw_message(
            message_id="gm-anfrage",
            subject="Angebotsanfrage Aluminiumprofile",
            sender="Julia Bauer <julia@musterkunde.de>",
            body="Guten Tag,\n\nbitte um ein Angebot über 200 Aluminiumprofile 40x40.\n\nViele Grüße",
        )
    }

    # 1. The worker job, as RQ would call it.
    result = await tasks._poll_mailbox_async(str(seeded["mailbox_id"]))
    assert result.processed == 1, f"unerwartet: {result}"
    assert result.failed == 0

    # 2. The mail landed classified, with a case and a draft.
    async with async_session_factory() as db:
        email = (await db.execute(select(EmailMessage))).scalar_one()
        assert email.wichtigkeits_kategorie is WichtigkeitsKategorie.ANTWORT_ERFORDERLICH
        assert email.typ is TypKategorie.ANFRAGE
        assert email.status is EmailStatus.WARTET_AUF_FREIGABE
        assert email.processed_at is not None
        assert email.embedding is not None

        case = (await db.execute(select(Case))).scalar_one()
        assert case.title == "Angebotsanfrage Aluminiumprofile"

        draft = (await db.execute(select(Draft))).scalar_one()
        assert draft.status is DraftStatus.ENTWURF

    # 3. The catalog entry reached the model and came back in the draft.
    assert "Aluminiumprofil 40x40" in draft.body
    assert "12.50" in draft.body or "12.5" in draft.body
    assert "Produkte aus der Wissensbasis" in draft.rag_context_summary

    # 4. The API shows it as awaiting approval.
    listed = await client.get("/api/drafts")
    assert listed.status_code == 200, listed.text
    assert [d["id"] for d in listed.json()] == [str(draft.id)]

    # 5. A human edits and approves it.
    edited = await client.put(
        f"/api/drafts/{draft.id}",
        json={"subject": "Re: Ihre Angebotsanfrage", "body": draft.body + "\n\nP.S. Gerne auch telefonisch."},
    )
    assert edited.status_code == 200, edited.text

    approved = await client.post(f"/api/drafts/{draft.id}/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "versendet"

    # 6. It actually went out, threaded onto the original mail.
    assert len(fake_gmail["sent"]) == 1
    sent = fake_gmail["sent"][0]
    assert sent["to_address"] == "julia@musterkunde.de"
    assert sent["subject"] == "Re: Ihre Angebotsanfrage"
    assert sent["thread_id"] == "thread-gm-anfrage"
    assert sent["in_reply_to_rfc822_id"] == "<gm-anfrage@kunde.example>"
    assert "P.S. Gerne auch telefonisch." in sent["body"], "die Bearbeitung des Nutzers ging verloren"

    # 7. The audit trail tells the whole story.
    async with async_session_factory() as db:
        actions = (await db.execute(select(ActionLog.action))).scalars().all()
    for expected in ("classified", "case_created", "draft_generated", "draft_approved", "draft_sent"):
        assert expected in actions, f"'{expected}' fehlt im Audit-Trail: {actions}"


async def test_a_newsletter_costs_no_model_call(seeded, fake_providers, fake_gmail, client) -> None:
    """The pre-filter's whole point is skipping the Claude call - proven
    here by the call counter, not by inspecting the pre-filter."""
    fake_gmail["messages"] = {
        "gm-nl": _raw_message(
            message_id="gm-nl",
            subject="Unsere Neuheiten im August",
            sender="newsletter@shop.example",
            body="Viele neue Artikel. Hier abmelden.",
            list_unsubscribe="<https://shop.example/unsubscribe>",
        )
    }

    result = await tasks._poll_mailbox_async(str(seeded["mailbox_id"]))

    assert result.processed == 1
    assert fake_providers["classify"] == 0, "der Prefilter hat den Claude-Aufruf nicht gespart"
    assert fake_providers["draft"] == 0

    async with async_session_factory() as db:
        email = (await db.execute(select(EmailMessage))).scalar_one()
        assert email.wichtigkeits_kategorie is WichtigkeitsKategorie.NEWSLETTER
        assert email.status is EmailStatus.ERLEDIGT
        assert (await db.execute(select(Draft))).scalars().all() == []


async def test_a_second_poll_reprocesses_nothing(seeded, fake_providers, fake_gmail) -> None:
    """Idempotency across whole poll cycles - the property that stops
    Claude tokens being spent twice on the same mail."""
    fake_gmail["messages"] = {
        "gm-1": _raw_message(
            message_id="gm-1",
            subject="Rechnung 4711",
            sender="buchhaltung@kunde.de",
            body="Anbei die Rechnung 4711.",
        )
    }

    first = await tasks._poll_mailbox_async(str(seeded["mailbox_id"]))
    calls_after_first = fake_providers["classify"]
    second = await tasks._poll_mailbox_async(str(seeded["mailbox_id"]))

    assert first.processed == 1
    assert second.processed == 0
    assert second.already_known == 1
    assert fake_providers["classify"] == calls_after_first, "zweiter Poll hat erneut klassifiziert"


async def test_two_mails_on_one_topic_share_a_case(seeded, fake_providers, fake_gmail) -> None:
    """Case matching, running for real against pgvector."""
    fake_gmail["messages"] = {
        "gm-a": _raw_message(
            message_id="gm-a",
            subject="Angebotsanfrage Aluminiumprofile",
            sender="julia@musterkunde.de",
            body="Bitte um Angebot über Aluminiumprofile 40x40.",
        )
    }
    await tasks._poll_mailbox_async(str(seeded["mailbox_id"]))

    fake_gmail["messages"] = {
        "gm-b": _raw_message(
            message_id="gm-b",
            subject="Nachfrage Aluminiumprofile",
            sender="julia@musterkunde.de",
            body="Ergänzend zu meiner Anfrage: die Aluminiumprofile bitte eloxiert.",
        )
    }
    await tasks._poll_mailbox_async(str(seeded["mailbox_id"]))

    async with async_session_factory() as db:
        cases = (await db.execute(select(Case))).scalars().all()
        emails = (await db.execute(select(EmailMessage))).scalars().all()

    assert len(emails) == 2
    assert len(cases) == 1, "zwei Mails zum selben Thema haben zwei Cases erzeugt"
    assert {e.case_id for e in emails} == {cases[0].id}


async def test_one_broken_mail_does_not_stop_the_others(
    seeded, fake_providers, fake_gmail, monkeypatch
) -> None:
    """The poison-message guarantee, at the level RQ actually runs."""
    fake_gmail["messages"] = {
        "gm-ok-1": _raw_message(
            message_id="gm-ok-1", subject="Rechnung", sender="a@kunde.de", body="Anbei."
        ),
        "gm-broken": _raw_message(
            message_id="gm-broken", subject="Kaputt", sender="b@kunde.de", body="Text."
        ),
        "gm-ok-2": _raw_message(
            message_id="gm-ok-2", subject="Rechnung 2", sender="c@kunde.de", body="Anbei."
        ),
    }

    original = gmail_client.get_message

    async def _sometimes_broken(service, message_id):
        if message_id == "gm-broken":
            raise RuntimeError("Gmail liefert Müll")
        return await original(service, message_id)

    monkeypatch.setattr(gmail_client, "get_message", _sometimes_broken)

    result = await tasks._poll_mailbox_async(str(seeded["mailbox_id"]))

    assert result.processed == 2
    assert result.failed == 1

    async with async_session_factory() as db:
        stored = (await db.execute(select(EmailMessage.gmail_message_id))).scalars().all()
        failures = (
            await db.execute(
                select(ActionLog).where(ActionLog.action == "mail_processing_failed")
            )
        ).scalars().all()
        mailbox = await db.get(Mailbox, seeded["mailbox_id"])

    assert set(stored) == {"gm-ok-1", "gm-ok-2"}
    assert len(failures) == 1
    assert failures[0].detail["gmail_message_id"] == "gm-broken"
    # The watermark must advance regardless, or the batch retries forever.
    assert mailbox.last_synced_at > datetime.now(UTC) - timedelta(minutes=5)
