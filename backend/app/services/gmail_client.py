"""Gmail OAuth2 connection + polling fetch + send.

MVP scope: polling (see app/workers/tasks.py) rather than Gmail
Pub/Sub push. Push is a documented follow-up, not implemented here (see
README "Ausbaustufen").

The google-api-python-client is synchronous; calls are pushed onto a
thread via asyncio.to_thread so they don't block the event loop.
"""
from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage as PyEmailMessage
from email.utils import parseaddr, parsedate_to_datetime

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request as GoogleAuthRequest
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.config import get_settings
from app.models.mailbox import Mailbox
from app.services import crypto

settings = get_settings()

GMAIL_API_SERVICE = "gmail"
GMAIL_API_VERSION = "v1"


def build_oauth_flow(state: str | None = None) -> Flow:
    client_config = {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [settings.google_redirect_uri],
        }
    }
    scopes = settings.google_oauth_scopes.split()
    flow = Flow.from_client_config(client_config, scopes=scopes, state=state)
    flow.redirect_uri = settings.google_redirect_uri
    return flow


def credentials_from_mailbox(mailbox: Mailbox) -> Credentials:
    access_token = crypto.decrypt(mailbox.access_token_encrypted) if mailbox.access_token_encrypted else None
    refresh_token = (
        crypto.decrypt(mailbox.refresh_token_encrypted) if mailbox.refresh_token_encrypted else None
    )
    return Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        scopes=(mailbox.granted_scopes or "").split() or None,
    )


def encrypted_fields_from_credentials(creds: Credentials) -> dict:
    return {
        "access_token_encrypted": crypto.encrypt(creds.token) if creds.token else None,
        "refresh_token_encrypted": crypto.encrypt(creds.refresh_token) if creds.refresh_token else None,
        "token_expiry": creds.expiry.replace(tzinfo=timezone.utc) if creds.expiry else None,
        "granted_scopes": " ".join(creds.scopes) if creds.scopes else None,
    }


def _refresh_if_needed(creds: Credentials) -> Credentials:
    if not creds.valid and creds.refresh_token:
        creds.refresh(GoogleAuthRequest())
    return creds


async def get_gmail_service(mailbox: Mailbox):
    """Builds an authorized Gmail API client for a mailbox, refreshing the
    access token first if it has expired. Returns (service, refreshed_creds)
    - callers should persist refreshed_creds back onto the mailbox if the
    token was rotated."""

    def _build():
        creds = credentials_from_mailbox(mailbox)
        creds = _refresh_if_needed(creds)
        service = build(GMAIL_API_SERVICE, GMAIL_API_VERSION, credentials=creds, cache_discovery=False)
        return service, creds

    return await asyncio.to_thread(_build)


@dataclass
class FetchedAttachment:
    filename: str
    content_type: str | None
    size_bytes: int | None
    gmail_attachment_id: str | None


@dataclass
class FetchedEmail:
    gmail_message_id: str
    gmail_thread_id: str | None
    rfc822_message_id: str | None
    subject: str | None
    sender_address: str
    sender_name: str | None
    raw_content: str
    snippet: str | None
    received_at: datetime
    attachments: list[FetchedAttachment] = field(default_factory=list)


def _extract_plain_text(payload: dict) -> str:
    """Depth-first search for a text/plain part; falls back to text/html
    stripped of tags if no plain-text part exists."""
    mime_type = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data")

    if mime_type == "text/plain" and body_data:
        return base64.urlsafe_b64decode(body_data.encode()).decode(errors="replace")

    for part in payload.get("parts", []) or []:
        text = _extract_plain_text(part)
        if text:
            return text

    if mime_type == "text/html" and body_data:
        import re

        html = base64.urlsafe_b64decode(body_data.encode()).decode(errors="replace")
        return re.sub("<[^<]+?>", " ", html)

    return ""


def _extract_attachments(payload: dict) -> list[FetchedAttachment]:
    attachments: list[FetchedAttachment] = []

    def walk(part: dict) -> None:
        filename = part.get("filename")
        body = part.get("body", {})
        if filename:
            attachments.append(
                FetchedAttachment(
                    filename=filename,
                    content_type=part.get("mimeType"),
                    size_bytes=body.get("size"),
                    gmail_attachment_id=body.get("attachmentId"),
                )
            )
        for sub in part.get("parts", []) or []:
            walk(sub)

    walk(payload)
    return attachments


def _header(headers: list[dict], name: str) -> str | None:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value")
    return None


def parse_gmail_message(raw: dict) -> FetchedEmail:
    payload = raw.get("payload", {})
    headers = payload.get("headers", [])

    from_header = _header(headers, "From") or ""
    sender_name, sender_address = parseaddr(from_header)

    date_header = _header(headers, "Date")
    try:
        received_at = parsedate_to_datetime(date_header) if date_header else None
        if received_at and received_at.tzinfo is None:
            received_at = received_at.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        received_at = None
    if received_at is None:
        internal_ms = raw.get("internalDate")
        received_at = (
            datetime.fromtimestamp(int(internal_ms) / 1000, tz=timezone.utc)
            if internal_ms
            else datetime.now(timezone.utc)
        )

    return FetchedEmail(
        gmail_message_id=raw["id"],
        gmail_thread_id=raw.get("threadId"),
        rfc822_message_id=_header(headers, "Message-ID"),
        subject=_header(headers, "Subject"),
        sender_address=sender_address or "unknown@unknown",
        sender_name=sender_name or None,
        raw_content=_extract_plain_text(payload),
        snippet=raw.get("snippet"),
        received_at=received_at,
        attachments=_extract_attachments(payload),
    )


async def list_new_message_ids(service, after_query: str | None = None, max_results: int = 25) -> list[str]:
    """Lists message IDs in the inbox, newest first. ``after_query`` can be
    a Gmail search query fragment (e.g. ``after:1699999999``) to only
    consider mail newer than the last successful poll."""

    def _list():
        query = "in:inbox" + (f" {after_query}" if after_query else "")
        resp = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
        return [m["id"] for m in resp.get("messages", [])]

    return await asyncio.to_thread(_list)


async def get_message(service, message_id: str) -> FetchedEmail:
    def _get():
        raw = service.users().messages().get(userId="me", id=message_id, format="full").execute()
        return parse_gmail_message(raw)

    return await asyncio.to_thread(_get)


async def send_reply(
    service,
    *,
    to_address: str,
    subject: str,
    body: str,
    thread_id: str | None,
    in_reply_to_rfc822_id: str | None,
    from_address: str,
) -> str:
    """Sends an approved draft as a real Gmail message and returns the
    resulting Gmail message id. Only ever called after explicit human
    approval - see app/api/routes/drafts.py."""

    def _send():
        msg = PyEmailMessage()
        msg["To"] = to_address
        msg["From"] = from_address
        msg["Subject"] = subject or "(kein Betreff)"
        if in_reply_to_rfc822_id:
            msg["In-Reply-To"] = in_reply_to_rfc822_id
            msg["References"] = in_reply_to_rfc822_id
        msg.set_content(body)

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        body_payload: dict = {"raw": raw}
        if thread_id:
            body_payload["threadId"] = thread_id

        sent = service.users().messages().send(userId="me", body=body_payload).execute()
        return sent["id"]

    try:
        return await asyncio.to_thread(_send)
    except HttpError as exc:
        raise RuntimeError(f"Gmail send failed: {exc}") from exc
