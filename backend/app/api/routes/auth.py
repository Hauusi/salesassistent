"""Gmail OAuth2 connect flow.

GET /api/auth/gmail/connect  -> redirects the browser to Google's consent screen
GET /api/auth/gmail/callback -> exchanges the auth code, stores the mailbox
                                 (tokens encrypted), redirects back to the
                                 frontend dashboard.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_db
from app.models.mailbox import Mailbox
from app.services import gmail_client
from app.services.action_log_service import log_action
from app.models.enums import ActionActor
from app.services.tenant_bootstrap import get_or_create_default_tenant, get_or_create_user

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/gmail/connect")
async def gmail_connect() -> RedirectResponse:
    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(
            status_code=500,
            detail="GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET not configured. See backend/.env.example.",
        )
    flow = gmail_client.build_oauth_flow()
    auth_url, _state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",  # ensures a refresh_token is issued even on repeat connects
    )
    return RedirectResponse(auth_url)


@router.get("/gmail/callback")
async def gmail_callback(request: Request, db: AsyncSession = Depends(get_db)) -> RedirectResponse:
    error = request.query_params.get("error")
    if error:
        return RedirectResponse(f"{settings.frontend_base_url}/connect?error={error}")

    code = request.query_params.get("code")
    if not code:
        raise HTTPException(status_code=400, detail="Missing 'code' in OAuth callback.")

    flow = gmail_client.build_oauth_flow()
    flow.fetch_token(code=code)
    creds = flow.credentials

    # Identify the mailbox's email address via the userinfo endpoint.
    import httpx

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

    return RedirectResponse(f"{settings.frontend_base_url}/connect?connected={email_address}")
