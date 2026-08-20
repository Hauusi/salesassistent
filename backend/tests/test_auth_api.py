"""Tests for the Gmail OAuth connect flow.

`state` is what ties a callback to the browser that started the flow. It
was generated and thrown away, and the callback accepted any code handed
to it - so a mailbox an attacker controls could be attached to this
installation, and every mail fetched from it would then be processed and
answerable from the dashboard.
"""
from __future__ import annotations

import pytest

from app.api.routes.auth import _STATE_COOKIE


@pytest.fixture(autouse=True)
def _oauth_configured(monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "google_client_id", "test-client-id", raising=False)
    monkeypatch.setattr(settings, "google_client_secret", "test-secret", raising=False)


async def test_connect_redirects_to_google_and_issues_a_state_cookie(api_client) -> None:
    response = await api_client.get("/api/auth/gmail/connect")

    assert response.status_code == 307
    assert response.headers["location"].startswith("https://accounts.google.com/o/oauth2/auth")
    assert _STATE_COOKIE in response.cookies


async def test_the_state_cookie_is_not_readable_by_scripts(api_client) -> None:
    response = await api_client.get("/api/auth/gmail/connect")
    set_cookie = response.headers["set-cookie"].lower()

    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie, "muss den Top-Level-Redirect von Google überleben"


async def test_callback_without_a_state_cookie_is_rejected(api_client) -> None:
    """The forged-callback case: an attacker's link carries a code and a
    state, but the victim's browser holds no matching cookie."""
    response = await api_client.get(
        "/api/auth/gmail/callback", params={"code": "angreifer-code", "state": "erfunden"}
    )

    assert response.status_code == 400
    assert "State" in response.json()["detail"]


async def test_callback_with_a_mismatched_state_is_rejected(api_client) -> None:
    await api_client.get("/api/auth/gmail/connect")  # sets a real cookie

    response = await api_client.get(
        "/api/auth/gmail/callback", params={"code": "irgendein-code", "state": "falscher-state"}
    )

    assert response.status_code == 400


async def test_callback_with_a_matching_state_but_bad_code_fails_cleanly(
    api_client, monkeypatch
) -> None:
    """A stale or replayed code is the common case and must not surface as
    a 500 - nor leak the provider's error text."""
    connect = await api_client.get("/api/auth/gmail/connect")
    state = connect.cookies[_STATE_COOKIE]

    from app.services import gmail_client

    class _Flow:
        def fetch_token(self, code):
            raise ValueError("invalid_grant: Bad Request https://oauth2.googleapis.com/token")

    monkeypatch.setattr(gmail_client, "build_oauth_flow", lambda state=None: _Flow())

    response = await api_client.get(
        "/api/auth/gmail/callback", params={"code": "abgelaufen", "state": state}
    )

    assert response.status_code == 400
    assert "oauth2.googleapis.com" not in response.text, "Provider-Fehlertext nach außen gegeben"


async def test_a_denied_consent_redirects_back_with_the_error(api_client) -> None:
    response = await api_client.get(
        "/api/auth/gmail/callback", params={"error": "access_denied"}, follow_redirects=False
    )

    assert response.status_code == 307
    assert "error=access_denied" in response.headers["location"]
