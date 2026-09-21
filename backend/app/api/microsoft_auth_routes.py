"""
Sign in with Microsoft, and connect an Outlook / Microsoft 365 mailbox to the desk.

    GET /auth/microsoft/start?intent=login&role=ADMIN&next=/welcome   -> {url}   identity only (openid profile email User.Read)
    GET /auth/microsoft/start?intent=connect&next=/welcome            -> {url}   needs a session; asks Mail.Read + Mail.Send
    GET /auth/microsoft/callback?code&state                            -> 302 to FRONTEND_URL/auth/callback#...

Two consents on purpose: logging in never asks for mail permissions, so the first screen is
harmless; "Connect Outlook" asks for Mail.Read / Mail.Send (delegated, no Google-style
restricted-scope review). The refresh token is stored encrypted (app.auth.mailbox_tokens),
identity is matched on the account's e-mail (`mail`, else `userPrincipalName`).

Token exchange and the Graph /me call are plain HTTPS so tests monkeypatch
`_exchange_code` / `_fetch_me` without touching Microsoft.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional
from urllib.parse import quote, urlencode

import httpx
from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import RedirectResponse

from app.api.auth_routes import _audit, new_user_id, registration_enabled
from app.api.google_auth_routes import frontend_url, make_state, read_state
from app.auth.accounts import allowed_register_roles, decode_token, issue_token
from app.auth.mailbox_tokens import encrypt_token
from app.config import ConfigurationError, auth_mode, env, get_repo
from app.connectors.email_connectors import MicrosoftGraphConnector, microsoft_oauth_client
from app.contracts.schemas import Role, UserMailbox, UserRecord

router = APIRouter(prefix="/auth/microsoft", tags=["auth"])
log = logging.getLogger("novaship.microsoft_auth")

GRAPH_ME = "https://graph.microsoft.com/v1.0/me"
LOGIN_SCOPES = ("openid", "profile", "email", "offline_access", "User.Read")
MAIL_SCOPES = ("offline_access", "User.Read", "Mail.Read", "Mail.Send")


def microsoft_sign_in_enabled() -> bool:
    if auth_mode() == "jwt":
        return False
    try:
        microsoft_oauth_client()
    except ConfigurationError:
        return False
    return True


def redirect_uri() -> str:
    return env("MICROSOFT_REDIRECT_URI", "http://localhost:8000/auth/microsoft/callback").strip()


def _authority(tenant: str) -> str:
    return f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0"


# ---------------------------------------------------------------- Microsoft calls (monkeypatched in tests)
def _exchange_code(code: str) -> dict[str, Any]:
    client_id, client_secret, tenant = microsoft_oauth_client()
    response = httpx.post(f"{_authority(tenant)}/token", data={
        "client_id": client_id, "client_secret": client_secret, "code": code,
        "redirect_uri": redirect_uri(), "grant_type": "authorization_code",
    }, timeout=20)
    response.raise_for_status()
    return response.json()


def _fetch_me(access_token: str) -> dict[str, Any]:
    response = httpx.get(GRAPH_ME, headers={"Authorization": f"Bearer {access_token}"}, params={"$select": "id,mail,userPrincipalName,displayName"}, timeout=20)
    response.raise_for_status()
    return response.json()


def _granted_scopes(tokens: dict[str, Any]) -> list[str]:
    return [s.rsplit("/", 1)[-1] if s.startswith("https://graph.microsoft.com/") else s for s in (tokens.get("scope") or "").split() if s]


def _fail(reason: str, st: Optional[dict[str, Any]] = None) -> RedirectResponse:
    """Login failures land on /login; a failed *connect* goes back to the page the signed-in user came from, with the reason."""
    if st and st.get("intent") == "connect":
        nxt = st.get("next") or "/welcome"
        if not nxt.startswith("/") or nxt.startswith("//"):
            nxt = "/welcome"
        sep = "&" if "?" in nxt else "?"
        return RedirectResponse(f"{frontend_url()}{nxt}{sep}mailbox_error={quote(reason)}", status_code=302)
    return RedirectResponse(f"{frontend_url()}/login?error={quote(reason)}", status_code=302)


# ---------------------------------------------------------------- routes
@router.get("/start")
def microsoft_start(intent: str = "login", role: Optional[str] = None, next: str = "/welcome", authorization: Optional[str] = Header(default=None)):
    """Build the Microsoft consent URL. `login` asks for identity only; `connect` needs a session and asks for mail permissions."""
    if auth_mode() == "jwt":
        raise HTTPException(403, detail={"error": "Microsoft sign-in is disabled in AUTH_MODE=jwt", "category": "AUTH_ERROR"})
    try:
        client_id, _secret, tenant = microsoft_oauth_client()
    except ConfigurationError as exc:
        raise HTTPException(503, detail={"error": str(exc), "category": "AUTH_ERROR", "recovery": "Set MICROSOFT_CLIENT_ID/SECRET in .env"})
    intent = (intent or "login").strip().lower()
    if intent not in {"login", "connect"}:
        raise HTTPException(400, detail={"error": "intent must be login or connect", "category": "AUTH_ERROR"})
    connect_user_id = None
    if authorization and authorization.lower().startswith("bearer "):
        payload = decode_token(authorization.split(" ", 1)[1].strip())
        if payload and get_repo().get_user(payload.get("sub", "")) and not get_repo().is_session_revoked(payload.get("sid", "")):
            connect_user_id = payload["sub"]
    if intent == "connect" and not connect_user_id:
        raise HTTPException(401, detail={"error": "sign in first, then connect your Outlook mailbox", "category": "AUTH_ERROR"})
    role_name = (role or "").strip().upper() or None
    if role_name and role_name not in allowed_register_roles():
        raise HTTPException(403, detail={"error": f"self-registration may only pick {', '.join(allowed_register_roles()) or 'no role'}", "category": "AUTH_ERROR"})
    if not next.startswith("/") or next.startswith("//"):
        next = "/welcome"
    state = make_state({"role": role_name, "next": next, "connect": connect_user_id if intent == "connect" else None, "intent": intent, "provider": "microsoft"})
    scopes = MAIL_SCOPES if intent == "connect" else LOGIN_SCOPES
    query = urlencode({
        "client_id": client_id, "response_type": "code", "redirect_uri": redirect_uri(), "response_mode": "query",
        "scope": " ".join(scopes), "state": state, "prompt": "consent" if intent == "connect" else "select_account",
    })
    return {"url": f"{_authority(tenant)}/authorize?{query}", "redirect_uri": redirect_uri(), "intent": intent, "connect": bool(connect_user_id and intent == "connect")}


@router.get("/callback")
def microsoft_callback(code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None, error_description: Optional[str] = None):
    """Microsoft redirects here. Success and failure both land on the frontend; JSON is never shown to the browser."""
    if auth_mode() == "jwt":
        return _fail("microsoft_disabled")
    if error:
        log.info("microsoft consent refused: %s %s", error, (error_description or "")[:120])
        return _fail("microsoft_denied")
    st = read_state(state)
    if not st or not code or st.get("provider") != "microsoft":
        return _fail("bad_state")
    try:
        tokens = _exchange_code(code)
    except Exception:
        return _fail("exchange_failed", st)
    try:
        me = _fetch_me(tokens.get("access_token") or "")
    except Exception:
        return _fail("userinfo_failed", st)
    address = (me.get("mail") or me.get("userPrincipalName") or "").strip().lower()
    if not address or "@" not in address:
        return _fail("email_unverified", st)
    granted = _granted_scopes(tokens)
    refresh_token = (tokens.get("refresh_token") or "").strip()
    repo = get_repo()
    intent = st.get("intent") or "login"

    if intent == "connect":
        user = repo.get_user(str(st.get("connect") or ""))
        if not user:
            return _fail("unknown_user", st)
        if not refresh_token or not ({"Mail.Read", "Mail.ReadWrite"} & set(granted)):
            return _fail("mail_permission_missing", st)
        mailbox = UserMailbox(user_id=user.id, tenant_id=user.tenant_id, provider="outlook", address=address, google_sub=me.get("id"),
                              refresh_token_enc=encrypt_token(refresh_token), scopes=granted, status="active", connected_at=datetime.utcnow())
        try:
            repo.save_mailbox(mailbox)
        except Exception as exc:
            if type(exc).__name__ == "MailboxStorageMissing":
                log.error("Outlook connect for %s lost: %s", user.id, exc)
                return _fail("mailbox_table_missing", st)
            log.exception("could not persist the connected Outlook mailbox for %s", user.id)
            return _fail("storage_failed", st)
        _audit(user.id, "MAILBOX_CONNECTED", {"address": address, "provider": "outlook", "scopes": granted, "can_send": mailbox.can_send()})
        fragment = urlencode({"connected": "outlook", "mailbox": address, "next": st.get("next") or "/welcome"})
        return RedirectResponse(f"{frontend_url()}/auth/callback#{fragment}", status_code=302)

    # login: create or open the desk account for this Microsoft identity
    created = False
    user = repo.get_user_by_email(address)
    if not user:
        if not registration_enabled():
            return _fail("registration_disabled")
        allowed = allowed_register_roles()
        role_name = (st.get("role") or (allowed[0] if allowed else "OPERATIONS_STAFF")).upper()
        if role_name not in allowed:
            return _fail("role_not_allowed")
        display_name = (me.get("displayName") or address.split("@", 1)[0]).strip()[:80]
        user = UserRecord(id=new_user_id(repo, address), email=address, display_name=display_name or address, roles=[Role(role_name)])
        try:
            repo.save_user(user)
        except NotImplementedError:
            return _fail("registration_unavailable")
        created = True
        _audit(user.id, "REGISTER", {"email": address, "roles": [role_name], "method": "microsoft"})
    _audit(user.id, "LOGIN", {"email": user.email, "roles": [r.value for r in user.roles], "method": "microsoft"})
    token, payload = issue_token(user.id)
    fragment = urlencode({
        "token": token, "expires_at": datetime.utcfromtimestamp(payload["exp"]).isoformat() + "Z",
        "next": st.get("next") or "/welcome", "new": "1" if created else "0", "provider": "microsoft", "mailbox": "",
    })
    return RedirectResponse(f"{frontend_url()}/auth/callback#{fragment}", status_code=302)
