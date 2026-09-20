"""
Email connector layer (adapter-based).

  EMAIL_PROVIDER = none | bundle | gmail
  * GmailConnector  - Gmail API via a user-authorized OAuth refresh token.
                      Primary real provider for inbound and approved outbound email.
  * BundleConnector - local SDOC fixture folder (demo / offline).

Outbound sending (Notifier) is separate and only invoked AFTER human approval.
"""
from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr
from pathlib import Path
from typing import Any, Iterable, Optional

import httpx
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials as GoogleCredentials
from googleapiclient.discovery import build as google_api_build

from app.file_security import UnsafeUpload, max_file_bytes, resolve_bundle_attachment, safe_filename, validate_attachment_count, validate_file_size


@dataclass
class InboundMessage:
    raw: dict[str, Any]                      # {email_id, from, to, cc, subject, body, attachments:[paths], conversation_id, provider_message_id}
    blobs: dict[str, bytes] = field(default_factory=dict)
    received_at: Optional[datetime] = None
    provider: str = "bundle"


class BaseConnector:
    name = "base"

    def fetch(self, since: Optional[datetime] = None, limit: int = 50) -> Iterable[InboundMessage]:  # pragma: no cover - interface
        raise NotImplementedError

    def send(self, to: list[str], subject: str, body: str, cc: Optional[list[str]] = None) -> dict[str, Any]:  # pragma: no cover - interface
        raise NotImplementedError


class BundleConnector(BaseConnector):
    name = "bundle"

    def __init__(self, folder: str | Path) -> None:
        self.folder = Path(folder)

    def fetch(self, since: Optional[datetime] = None, limit: int = 50) -> Iterable[InboundMessage]:
        files = sorted((self.folder / "inbox").glob("email_*.json"))[:limit]
        for p in files:
            raw = json.loads(p.read_text(encoding="utf-8"))
            validate_attachment_count(len(raw.get("attachments", [])))
            blobs = {}
            for attachment_path in raw.get("attachments", []):
                candidate = resolve_bundle_attachment(self.folder, attachment_path)
                if candidate.exists():
                    validate_file_size(candidate.stat().st_size)
                    data = candidate.read_bytes()
                    blobs[attachment_path] = data
            yield InboundMessage(raw=raw, blobs=blobs, provider="bundle")


class GmailConnector(BaseConnector):
    """Gmail API connector using a one-time user grant and a server-side refresh token.

    `GmailConnector()` / `from_env()` is the shared desk mailbox configured in .env.
    `from_mailbox()` is a user's own Gmail connected through Google sign-in.
    """

    name = "gmail"
    TOKEN_URI = "https://oauth2.googleapis.com/token"
    SCOPES = (
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
    )

    def __init__(self, *, client_id: Optional[str] = None, client_secret: Optional[str] = None,
                 refresh_token: Optional[str] = None, address: Optional[str] = None) -> None:
        if client_id is None and client_secret is None and refresh_token is None and address is None:
            required = ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN", "GMAIL_ADDRESS")
            missing = [name for name in required if not os.environ.get(name, "").strip()]
            if missing:
                from app.config import ConfigurationError

                raise ConfigurationError(f"Gmail provider requires {', '.join(missing)}")
            client_id = os.environ["GMAIL_CLIENT_ID"]
            client_secret = os.environ["GMAIL_CLIENT_SECRET"]
            refresh_token = os.environ["GMAIL_REFRESH_TOKEN"]
            address = os.environ["GMAIL_ADDRESS"]
        if not (client_id and client_secret and refresh_token and address):
            from app.config import ConfigurationError

            raise ConfigurationError("Gmail connector requires client id, client secret, refresh token and address")
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        self.refresh_token = refresh_token.strip()
        self.address = address.strip()
        self._credentials: Optional[GoogleCredentials] = None
        self._service = None
        self._token_expires_at = 0.0

    @classmethod
    def from_env(cls) -> "GmailConnector":
        return cls()

    @classmethod
    def from_mailbox(cls, mailbox) -> "GmailConnector":
        """Connector for a user's connected Gmail (see app.api.google_auth_routes)."""
        from app.auth.mailbox_tokens import decrypt_token

        client_id, client_secret = google_oauth_client()
        return cls(client_id=client_id, client_secret=client_secret, refresh_token=decrypt_token(mailbox.refresh_token_enc), address=mailbox.address)

    def token(self) -> str:
        if self._credentials and self._credentials.token and time.monotonic() < self._token_expires_at:
            return self._credentials.token
        if self._credentials is None:
            self._credentials = GoogleCredentials(
                token=None,
                refresh_token=self.refresh_token,
                token_uri=self.TOKEN_URI,
                client_id=self.client_id,
                client_secret=self.client_secret,
                scopes=self.SCOPES,
            )
        self._credentials.refresh(GoogleAuthRequest())
        if not self._credentials.token:
            raise RuntimeError("Gmail OAuth refresh returned no access token")
        lifetime = 3600.0
        if self._credentials.expiry:
            expiry = self._credentials.expiry
            now = datetime.now(expiry.tzinfo) if expiry.tzinfo else datetime.utcnow()
            lifetime = max((expiry - now).total_seconds(), 1.0)
        self._token_expires_at = time.monotonic() + max(lifetime - min(60.0, lifetime * 0.1), 0.0)
        self._service = None
        return self._credentials.token

    def _invalidate_token(self) -> None:
        if self._credentials:
            self._credentials.token = None
            self._credentials.expiry = None
        self._token_expires_at = 0.0
        self._service = None

    def _service_client(self):
        self.token()
        if self._service is None:
            self._service = google_api_build("gmail", "v1", credentials=self._credentials, cache_discovery=False)
        return self._service

    @staticmethod
    def _status_code(exc: Exception) -> Optional[int]:
        response = getattr(exc, "resp", None)
        status = getattr(response, "status", None) or getattr(response, "status_code", None)
        return int(status) if status is not None else None

    def _execute(self, request_factory):
        """Execute a Gmail request, refreshing and retrying exactly once after a 401."""
        for attempt in range(2):
            try:
                return request_factory(self._service_client()).execute()
            except Exception as exc:
                if self._status_code(exc) != 401 or attempt:
                    raise
                self._invalidate_token()
        raise RuntimeError("unreachable")

    def fetch(self, since: Optional[datetime] = None, limit: int = 50) -> Iterable[InboundMessage]:
        if limit <= 0:
            return
        query = None
        if since:
            aware = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
            query = f"after:{int(aware.timestamp())}"
        listed = self._execute(lambda service: service.users().messages().list(
            userId="me", labelIds=["INBOX"], maxResults=min(limit, 500), q=query
        ))
        for summary in (listed.get("messages") or [])[:limit]:
            message_id = summary["id"]
            message = self._execute(lambda service, mid=message_id: service.users().messages().get(
                userId="me", id=mid, format="full"
            ))
            payload = message.get("payload") or {}
            headers = _gmail_headers(payload)
            from_name, from_address = parseaddr(headers.get("from", ""))
            to = [address for _name, address in getaddresses([headers.get("to", "")]) if address]
            cc = [address for _name, address in getaddresses([headers.get("cc", "")]) if address]
            attachment_parts = [part for part in _gmail_leaf_parts(payload) if part.get("filename")]
            validate_attachment_count(len(attachment_parts))
            blobs: dict[str, bytes] = {}
            paths: list[str] = []
            for part in attachment_parts:
                attachment_body = part.get("body") or {}
                validate_file_size(int(attachment_body.get("size") or 0))
                encoded = attachment_body.get("data")
                if not encoded and attachment_body.get("attachmentId"):
                    attachment = self._execute(
                        lambda service, aid=attachment_body["attachmentId"], mid=message_id:
                        service.users().messages().attachments().get(userId="me", messageId=mid, id=aid)
                    )
                    validate_file_size(int(attachment.get("size") or 0))
                    encoded = attachment.get("data")
                encoded = encoded or ""
                if len(encoded) > ((max_file_bytes() + 2) // 3) * 4 + 4:
                    raise UnsafeUpload("Gmail attachment exceeds maximum size")
                data = _gmail_b64decode(encoded)
                validate_file_size(len(data))
                name = safe_filename(_decoded_header(part.get("filename") or "attachment.bin"))
                path = _unique_attachment_path(name, blobs)
                blobs[path] = data
                paths.append(path)
            message_header_id = headers.get("message-id") or message_id
            raw = {
                "email_id": _safe_id(message_header_id),
                "provider_message_id": message_id,
                "conversation_id": message.get("threadId"),
                "from": from_address,
                "from_name": _decoded_header(from_name) or None,
                "to": to,
                "cc": cc,
                "subject": _decoded_header(headers.get("subject", "")),
                "body": _gmail_message_body(payload),
                "attachments": paths,
            }
            received_at = None
            if message.get("internalDate"):
                received_at = datetime.fromtimestamp(int(message["internalDate"]) / 1000, tz=timezone.utc).replace(tzinfo=None)
            yield InboundMessage(raw=raw, blobs=blobs, received_at=received_at, provider="gmail")

    def send(self, to: list[str], subject: str, body: str, cc: Optional[list[str]] = None) -> dict[str, Any]:
        message = EmailMessage()
        message["From"] = self.address
        message["To"] = ", ".join(to)
        if cc:
            message["Cc"] = ", ".join(cc)
        message["Subject"] = subject
        message.set_content(body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        sent = self._execute(lambda service: service.users().messages().send(userId="me", body={"raw": raw}))
        if not sent.get("id"):
            raise RuntimeError("Gmail API did not return an accepted message id")
        return {"status": 200, "id": sent["id"], "thread_id": sent.get("threadId")}


class MicrosoftGraphConnector(BaseConnector):
    """Outlook / Microsoft 365 mailbox through Microsoft Graph, authorised by a user's delegated refresh token.

    Microsoft rotates refresh tokens on every refresh; the new one is handed to `on_refresh_token`
    so the caller can persist it (mailbox_service does).
    """

    name = "outlook"
    GRAPH = "https://graph.microsoft.com/v1.0"
    SCOPES = ("Mail.Read", "Mail.Send", "offline_access", "User.Read")
    SEND_SCOPE = "Mail.Send"
    MESSAGE_SELECT = "id,internetMessageId,conversationId,from,toRecipients,ccRecipients,subject,body,receivedDateTime,hasAttachments"

    def __init__(self, *, client_id: str, client_secret: str, refresh_token: str, address: str, tenant: str = "common",
                 on_refresh_token=None) -> None:
        if not (client_id and client_secret and refresh_token and address):
            from app.config import ConfigurationError

            raise ConfigurationError("Microsoft Graph connector requires client id, client secret, refresh token and address")
        self.client_id, self.client_secret = client_id.strip(), client_secret.strip()
        self.refresh_token, self.address = refresh_token.strip(), address.strip()
        self.tenant = (tenant or "common").strip()
        self.on_refresh_token = on_refresh_token
        self._access_token: Optional[str] = None
        self._token_expires_at = 0.0

    @classmethod
    def from_mailbox(cls, mailbox, on_refresh_token=None) -> "MicrosoftGraphConnector":
        from app.auth.mailbox_tokens import decrypt_token

        client_id, client_secret, tenant = microsoft_oauth_client()
        return cls(client_id=client_id, client_secret=client_secret, refresh_token=decrypt_token(mailbox.refresh_token_enc),
                   address=mailbox.address, tenant=tenant, on_refresh_token=on_refresh_token)

    # ---- auth
    def token(self) -> str:
        if self._access_token and time.monotonic() < self._token_expires_at:
            return self._access_token
        response = httpx.post(f"https://login.microsoftonline.com/{self.tenant}/oauth2/v2.0/token", data={
            "client_id": self.client_id, "client_secret": self.client_secret, "grant_type": "refresh_token",
            "refresh_token": self.refresh_token, "scope": " ".join(self.SCOPES),
        }, timeout=20)
        response.raise_for_status()
        payload = response.json()
        self._access_token = payload.get("access_token")
        if not self._access_token:
            raise RuntimeError("Microsoft token endpoint returned no access token")
        lifetime = float(payload.get("expires_in") or 3600)
        self._token_expires_at = time.monotonic() + max(lifetime - min(60.0, lifetime * 0.1), 0.0)
        rotated = (payload.get("refresh_token") or "").strip()
        if rotated and rotated != self.refresh_token:
            self.refresh_token = rotated
            if self.on_refresh_token:
                self.on_refresh_token(rotated)
        return self._access_token

    def _request(self, method: str, path: str, **kwargs) -> Any:
        """One Graph call; refreshes and retries exactly once after a 401."""
        for attempt in range(2):
            headers = {"Authorization": f"Bearer {self.token()}", **kwargs.pop("headers", {})}
            response = httpx.request(method, path if path.startswith("http") else f"{self.GRAPH}{path}", headers=headers, timeout=30, **kwargs)
            if response.status_code == 401 and not attempt:
                self._access_token, self._token_expires_at = None, 0.0
                continue
            if response.status_code >= 400:
                raise RuntimeError(f"Microsoft Graph {method} {path} -> HTTP {response.status_code}")
            return response
        raise RuntimeError("unreachable")

    # ---- inbound
    def fetch(self, since: Optional[datetime] = None, limit: int = 50) -> Iterable[InboundMessage]:
        if limit <= 0:
            return
        params = {"$top": str(min(limit, 100)), "$orderby": "receivedDateTime desc", "$select": self.MESSAGE_SELECT}
        if since:
            aware = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
            params["$filter"] = f"receivedDateTime ge {aware.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        listed = self._request("GET", "/me/mailFolders/inbox/messages", params=params).json()
        for message in (listed.get("value") or [])[:limit]:
            message_id = message["id"]
            sender = ((message.get("from") or {}).get("emailAddress") or {})
            to = [r["emailAddress"]["address"] for r in message.get("toRecipients") or [] if r.get("emailAddress", {}).get("address")]
            cc = [r["emailAddress"]["address"] for r in message.get("ccRecipients") or [] if r.get("emailAddress", {}).get("address")]
            body_obj = message.get("body") or {}
            body = body_obj.get("content") or ""
            if str(body_obj.get("contentType", "")).lower() == "html":
                body = _strip_html(body)
            blobs: dict[str, bytes] = {}
            paths: list[str] = []
            if message.get("hasAttachments"):
                attachments = self._request("GET", f"/me/messages/{message_id}/attachments").json().get("value") or []
                files = [a for a in attachments if a.get("@odata.type", "").endswith("fileAttachment") and a.get("contentBytes")]
                validate_attachment_count(len(files))
                for a in files:
                    validate_file_size(int(a.get("size") or 0))
                    data = base64.b64decode(a["contentBytes"])
                    validate_file_size(len(data))
                    name = safe_filename(a.get("name") or "attachment.bin")
                    path = _unique_attachment_path(name, blobs)
                    blobs[path] = data
                    paths.append(path)
            raw = {
                "email_id": _safe_id(message.get("internetMessageId") or message_id),
                "provider_message_id": message_id,
                "conversation_id": message.get("conversationId"),
                "from": sender.get("address") or "",
                "from_name": sender.get("name") or None,
                "to": to,
                "cc": cc,
                "subject": message.get("subject") or "",
                "body": body,
                "attachments": paths,
            }
            received_at = None
            if message.get("receivedDateTime"):
                try:
                    received_at = datetime.fromisoformat(message["receivedDateTime"].replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)
                except ValueError:
                    received_at = None
            yield InboundMessage(raw=raw, blobs=blobs, received_at=received_at, provider="outlook")

    # ---- outbound (only after human approval)
    def send(self, to: list[str], subject: str, body: str, cc: Optional[list[str]] = None) -> dict[str, Any]:
        payload = {
            "message": {
                "subject": subject,
                "body": {"contentType": "Text", "content": body},
                "toRecipients": [{"emailAddress": {"address": a}} for a in to],
                "ccRecipients": [{"emailAddress": {"address": a}} for a in (cc or [])],
            },
            "saveToSentItems": True,
        }
        response = self._request("POST", "/me/sendMail", json=payload)
        if response.status_code != 202:
            raise RuntimeError(f"Microsoft Graph sendMail returned HTTP {response.status_code}")
        return {"status": 202, "id": response.headers.get("request-id") or response.headers.get("client-request-id") or "accepted", "from": self.address}


def microsoft_oauth_client() -> tuple[str, str, str]:
    """Entra app registration used for Microsoft sign-in and Outlook mailboxes: (client_id, client_secret, tenant)."""
    client_id = os.environ.get("MICROSOFT_CLIENT_ID", "").strip()
    client_secret = os.environ.get("MICROSOFT_CLIENT_SECRET", "").strip()
    tenant = os.environ.get("MICROSOFT_TENANT", "common").strip() or "common"
    if not client_id or not client_secret:
        from app.config import ConfigurationError

        raise ConfigurationError("Microsoft sign-in requires MICROSOFT_CLIENT_ID and MICROSOFT_CLIENT_SECRET")
    return client_id, client_secret, tenant


def connector_for_mailbox(mailbox, on_refresh_token=None) -> BaseConnector:
    """The connector that reads and sends for a user's connected mailbox, by provider."""
    provider = (getattr(mailbox, "provider", "") or "gmail").lower()
    if provider == "outlook":
        return MicrosoftGraphConnector.from_mailbox(mailbox, on_refresh_token=on_refresh_token)
    if provider == "gmail":
        return GmailConnector.from_mailbox(mailbox)
    from app.config import ConfigurationError

    raise ConfigurationError(f"unsupported mailbox provider '{provider}'")


def shared_mailbox_configured() -> bool:
    """True when the desk still has a shared inbound mailbox (EMAIL_PROVIDER=gmail/bundle with its settings present)."""
    provider = os.environ.get("EMAIL_PROVIDER", "none").strip().lower()
    if provider == "bundle":
        return True
    if provider != "gmail":
        return False
    return all(os.environ.get(name, "").strip() for name in ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN", "GMAIL_ADDRESS"))


def google_oauth_client() -> tuple[str, str]:
    """OAuth client used for Google sign-in / per-user mailboxes; falls back to the shared Gmail client."""
    client_id = (os.environ.get("GOOGLE_OAUTH_CLIENT_ID") or os.environ.get("GMAIL_CLIENT_ID") or "").strip()
    client_secret = (os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET") or os.environ.get("GMAIL_CLIENT_SECRET") or "").strip()
    if not client_id or not client_secret:
        from app.config import ConfigurationError

        raise ConfigurationError("Google sign-in requires GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET (or the GMAIL_* client)")
    return client_id, client_secret


def get_connector() -> Optional[BaseConnector]:
    provider = os.environ.get("EMAIL_PROVIDER", "none").lower()
    if provider == "none":
        return None
    if provider == "gmail":
        return GmailConnector()
    if provider == "bundle":
        from app.config import BUNDLE_DIR

        return BundleConnector(BUNDLE_DIR)
    from app.config import ConfigurationError

    raise ConfigurationError("EMAIL_PROVIDER must be one of: none, bundle, gmail")


def get_outbound_connector(mode: Optional[str] = None) -> Optional[BaseConnector]:
    selected = (mode or os.environ.get("EMAIL_SEND_MODE", "simulate")).strip().lower()
    if selected == "simulate":
        return None
    if selected in ("gmail", "live"):
        return GmailConnector()
    from app.config import ConfigurationError

    raise ConfigurationError("EMAIL_SEND_MODE must be one of: simulate, gmail (alias: live)")


def _decoded_header(value: str) -> str:
    try:
        return str(make_header(decode_header(value)))
    except (LookupError, UnicodeDecodeError):
        return value


def _gmail_headers(payload: dict[str, Any]) -> dict[str, str]:
    return {str(item.get("name", "")).lower(): _decoded_header(str(item.get("value", ""))) for item in payload.get("headers", [])}


def _gmail_leaf_parts(payload: dict[str, Any]) -> Iterable[dict[str, Any]]:
    parts = payload.get("parts") or []
    if not parts:
        yield payload
        return
    for part in parts:
        yield from _gmail_leaf_parts(part)


def _gmail_b64decode(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    return base64.b64decode(padded, altchars=b"-_", validate=True)


def _gmail_part_text(part: dict[str, Any]) -> str:
    encoded = (part.get("body") or {}).get("data")
    if not encoded:
        return ""
    data = _gmail_b64decode(encoded)
    content_type = _gmail_headers(part).get("content-type", "")
    charset = "utf-8"
    if "charset=" in content_type.lower():
        charset = content_type.lower().split("charset=", 1)[1].split(";", 1)[0].strip(' \t"\'') or "utf-8"
    try:
        return data.decode(charset, errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")


def _gmail_message_body(payload: dict[str, Any]) -> str:
    plain: list[str] = []
    html: list[str] = []
    for part in _gmail_leaf_parts(payload):
        if part.get("filename"):
            continue
        mime = str(part.get("mimeType", "")).lower()
        text = _gmail_part_text(part)
        if text and mime == "text/plain":
            plain.append(text)
        elif text and mime == "text/html":
            html.append(text)
    return "\n".join(plain).strip() or _strip_html("\n".join(html))


def _unique_attachment_path(name: str, blobs: dict[str, bytes]) -> str:
    path = f"attachments/{name}"
    if path not in blobs:
        return path
    stem, suffix = Path(name).stem, Path(name).suffix
    index = 2
    while f"attachments/{stem}_{index}{suffix}" in blobs:
        index += 1
    return f"attachments/{stem}_{index}{suffix}"


def _strip_html(html: str) -> str:
    from app.readers.textutil import strip_html

    return strip_html(html)


def _safe_id(s: str) -> str:
    import re

    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s).strip("_")[:120]
