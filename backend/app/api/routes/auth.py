"""Gmail OAuth2 connect flow.

GET /api/auth/gmail/connect  -> redirects the browser to Google's consent screen
GET /api/auth/gmail/callback -> exchanges the auth code, stores the mailbox
                                 (tokens encrypted), redirects back to the
                                 frontend dashboard.

The `state` parameter is what ties the callback to the browser that
started the flow. It was previously generated and discarded, and the
callback accepted any code it was handed - so anyone who could get this
endpoint called with a code of their choosing could attach a mailbox they
control to this installation, and every mail fetched from it would be
processed and answerable from the dashboard. It is now issued as a
short-lived HttpOnly cookie and compared on the way back.
"""
from __future__ import annotations

import logging
import secrets

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_db
from app.models.enums import ActionActor
from app.models.mailbox import Mailbox
from app.services import gmail_client
from app.services.action_log_service import log_action
from app.services.tenant_bootstrap import get_or_create_default_tenant, get_or_create_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Name of the cookie carrying the OAuth state. A browser cannot be made to
# send a cookie for this origin that an attacker chose, so comparing the
# returned `state` against it is what proves the callback belongs to a flow
# this browser actually started.
_STATE_COOKIE = "gmail_oauth_state"
_STATE_COOKIE_MAX_AGE = 600  # a consent screen the user leaves open that long is stale


@router.get("/gmail/connect")
async def gmail_connect() -> RedirectResponse:
    settings = get_settings()
    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(
            status_code=500,
            detail="GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET not configured. See backend/.env.example.",
        )
    flow = gmail_client.build_oauth_flow()
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",  # ensures a refresh_token is issued even on repeat connects
    )

    response = RedirectResponse(auth_url)
    response.set_cookie(
        _STATE_COOKIE,
        state,
        max_age=_STATE_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",  # must survive the top-level redirect back from Google
        secure=settings.api_base_url.startswith("https://"),
        path="/api/auth",
    )
    return response


@router.get("/gmail/callback")
async def gmail_callback(request: Request, db: AsyncSession = Depends(get_db)) -> RedirectResponse:
    settings = get_settings()
    error = request.query_params.get("error")
    if error:
        return RedirectResponse(f"{settings.frontend_base_url}/connect?error={error}")

    # Verify the flow belongs to this browser before touching the code.
    expected_state = request.cookies.get(_STATE_COOKIE)
    received_state = request.query_params.get("state")
    if not expected_state or not received_state or not secrets.compare_digest(
        expected_state, received_state
    ):
        logger.warning("oauth_state_mismatch has_cookie=%s has_param=%s",
                       bool(expected_state), bool(received_state))
        raise HTTPException(
            status_code=400,
            detail="OAuth-State stimmt nicht. Bitte den Verbindungsvorgang neu starten.",
        )

    code = request.query_params.get("code")
    if not code:
        raise HTTPException(status_code=400, detail="Missing 'code' in OAuth callback.")

    flow = gmail_client.build_oauth_flow(state=received_state)
    try:
        flow.fetch_token(code=code)
    except Exception as exc:
        # The code is single-use and short-lived; a stale or replayed one is
        # the common case here, not a server fault.
        logger.warning("oauth_token_exchange_failed error=%s", type(exc).__name__)
        raise HTTPException(
            status_code=400, detail="Autorisierungscode konnte nicht eingelöst werden."
        ) from exc
    creds = flow.credentials

    # Identify the mailbox's email address via the userinfo endpoint.
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {creds.token}"},
        )
        resp.raise_for_status()
        email_address = resp.json()["email"]

    tenant = await get_or_create_default_tenant(db)
    user = await get_or_create_user(db, tenant, email=email_address)

    result = await db.execute(
        select(Mailbox).where(Mailbox.tenant_id == tenant.id, Mailbox.email_address == email_address)
    )
    mailbox = result.scalar_one_or_none()

    token_fields = gmail_client.encrypted_fields_from_credentials(creds)
    if mailbox is None:
        mailbox = Mailbox(
            tenant_id=tenant.id,
            user_id=user.id,
            email_address=email_address,
            is_active=True,
            **token_fields,
        )
        db.add(mailbox)
    else:
        for key, value in token_fields.items():
            setattr(mailbox, key, value)
        mailbox.is_active = True

    await db.flush()
    await log_action(
        db,
        tenant_id=tenant.id,
        actor=ActionActor.USER,
        entity_type="mailbox",
        entity_id=mailbox.id,
        action="mailbox_connected",
        detail={"email_address": email_address},
    )
    await db.commit()

    response = RedirectResponse(f"{settings.frontend_base_url}/connect?connected={email_address}")
    response.delete_cookie(_STATE_COOKIE, path="/api/auth")
    return response
