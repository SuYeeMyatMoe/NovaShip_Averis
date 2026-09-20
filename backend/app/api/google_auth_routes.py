"""
Sign up / sign in with Google, which also connects the user's own Gmail to the desk.

    GET /auth/google/start?role=ADMIN&next=/welcome  -> {url}   (Authorization header = connect to the current account)
    GET /auth/google/callback?code&state              -> 302 to FRONTEND_URL/auth/callback#token=...

One consent grants identity (openid email profile) plus gmail.readonly and
gmail.send. The refresh token is stored encrypted (app.auth.mailbox_tokens);
identity is matched on the verified Google email. A new address becomes a
desk account with a role from REGISTER_ALLOWED_ROLES, an existing address
simply logs in, and either way the mailbox is (re)connected.

The token exchange and userinfo calls are plain HTTPS requests so tests can
monkeypatch `_exchange_code` / `_fetch_userinfo` without touching Google.
"""
from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from datetime import datetime
from typing import Any, Optional
from urllib.parse import quote, urlencode

import httpx
from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import RedirectResponse

from app.api.auth_routes import _audit, new_user_id, registration_enabled
from app.auth.accounts import _b64d, _b64e, _sign, allowed_register_roles, decode_token, issue_token
from app.auth.mailbox_tokens import encrypt_token
from app.config import ConfigurationError, auth_mode, cors_allowed_origins, env, get_repo
from app.connectors.email_connectors import google_oauth_client
from app.contracts.schemas import Role, UserMailbox, UserRecord

router = APIRouter(prefix="/auth/google", tags=["auth"])
log = logging.getLogger("novaship.google_auth")

AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
USERINFO_URI = "https://www.googleapis.com/oauth2/v3/userinfo"
SCOPES = (
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
)
STATE_TTL_S = 600

_nonce_lock = threading.Lock()
_used_nonces: dict[str, float] = {}  # nonce -> expiry; single-use states


def google_sign_in_enabled() -> bool:
    if auth_mode() == "jwt":
        return False
    try:
        google_oauth_client()
    except ConfigurationError:
        return False
    return True


def redirect_uri() -> str:
    return env("GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:8000/auth/google/callback").strip()


def frontend_url() -> str:
    configured = env("FRONTEND_URL", "").strip()
    return (configured or cors_allowed_origins()[0]).rstrip("/")


# ---------------------------------------------------------------- signed state
def make_state(payload: dict[str, Any]) -> str:
    body = _b64e(json.dumps({**payload, "nonce": secrets.token_hex(8), "exp": int(time.time()) + STATE_TTL_S}, separators=(",", ":")).encode("utf-8"))
    return f"{body}.{_sign(body)}"


def read_state(state: Optional[str]) -> Optional[dict[str, Any]]:
    """Return the payload when the signature is valid, unexpired and the nonce unused; None otherwise."""
    import hmac

    if not state or "." not in state:
        return None
    body, sig = state.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(body)):
        return None
    try:
        payload = json.loads(_b64d(body))
    except Exception:
        return None
    now = time.time()
    if not isinstance(payload, dict) or payload.get("exp", 0) < now:
        return None
    nonce = str(payload.get("nonce") or "")
    with _nonce_lock:
        for used, expiry in list(_used_nonces.items()):
            if expiry < now:
                _used_nonces.pop(used, None)
        if nonce in _used_nonces:
            return None
        _used_nonces[nonce] = float(payload["exp"])
    return payload


# ---------------------------------------------------------------- Google calls (monkeypatched in tests)
def _exchange_code(code: str) -> dict[str, Any]:
    client_id, client_secret = google_oauth_client()
    response = httpx.post(TOKEN_URI, data={
        "code": code, "client_id": client_id, "client_secret": client_secret,
        "redirect_uri": redirect_uri(), "grant_type": "authorization_code",
    }, timeout=20)
    response.raise_for_status()
    return response.json()


def _fetch_userinfo(access_token: str) -> dict[str, Any]:
    response = httpx.get(USERINFO_URI, headers={"Authorization": f"Bearer {access_token}"}, timeout=20)
    response.raise_for_status()
    return response.json()


# ---------------------------------------------------------------- routes
@router.get("/start")
def google_start(role: Optional[str] = None, next: str = "/welcome", authorization: Optional[str] = Header(default=None)):
    """Build the Google consent URL. With a valid session token the grant connects Gmail to that existing account."""
    if auth_mode() == "jwt":
        raise HTTPException(403, detail={"error": "Google sign-in is disabled in AUTH_MODE=jwt", "category": "AUTH_ERROR"})
    try:
        client_id, _secret = google_oauth_client()
    except ConfigurationError as exc:
        raise HTTPException(503, detail={"error": str(exc), "category": "AUTH_ERROR", "recovery": "Set GOOGLE_OAUTH_CLIENT_ID/SECRET in .env"})
    connect_user_id = None
    if authorization and authorization.lower().startswith("bearer "):
        payload = decode_token(authorization.split(" ", 1)[1].strip())
        if payload and get_repo().get_user(payload.get("sub", "")) and not get_repo().is_session_revoked(payload.get("sid", "")):
            connect_user_id = payload["sub"]
    role_name = (role or "").strip().upper() or None
    if role_name and role_name not in allowed_register_roles():
        raise HTTPException(403, detail={"error": f"self-registration may only pick {', '.join(allowed_register_roles()) or 'no role'}", "category": "AUTH_ERROR"})
    if not next.startswith("/") or next.startswith("//"):
        next = "/welcome"
    state = make_state({"role": role_name, "next": next, "connect": connect_user_id})
    query = urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri(), "response_type": "code", "scope": " ".join(SCOPES),
        "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true", "state": state,
    })
    return {"url": f"{AUTH_URI}?{query}", "redirect_uri": redirect_uri(), "connect": bool(connect_user_id)}


def _fail(reason: str) -> RedirectResponse:
    return RedirectResponse(f"{frontend_url()}/login?error={quote(reason)}", status_code=302)


@router.get("/callback")
def google_callback(code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None):
    """Google redirects here. Never returns JSON to the browser: success and failure both land on the frontend."""
    if auth_mode() == "jwt":
        return _fail("google_disabled")
    if error:
        return _fail("google_denied")
    st = read_state(state)
    if not st or not code or st.get("provider") not in (None, "google"):
        return _fail("bad_state")
    try:
        tokens = _exchange_code(code)
    except Exception:
        return _fail("exchange_failed")
    refresh_token = (tokens.get("refresh_token") or "").strip()
    if not refresh_token:
        return _fail("no_refresh_token")  # the grant was reused without consent; Google only issues it on prompt=consent
    try:
        info = _fetch_userinfo(tokens.get("access_token") or "")
    except Exception:
        return _fail("userinfo_failed")
    address = (info.get("email") or "").strip().lower()
    if not address or not info.get("email_verified", False):
        return _fail("email_unverified")
    granted = [s for s in (tokens.get("scope") or "").split() if s]

    repo = get_repo()
    created = False
    if st.get("connect"):
        user = repo.get_user(str(st["connect"]))
        if not user:
            return _fail("unknown_user")
    else:
        user = repo.get_user_by_email(address)
        if not user:
            if not registration_enabled():
                return _fail("registration_disabled")
            allowed = allowed_register_roles()
            role_name = (st.get("role") or (allowed[0] if allowed else "OPERATIONS_STAFF")).upper()
            if role_name not in allowed:
                return _fail("role_not_allowed")
            display_name = (info.get("name") or address.split("@", 1)[0]).strip()[:80]
            user = UserRecord(id=new_user_id(repo, address), email=address, display_name=display_name or address, roles=[Role(role_name)])
            try:
                repo.save_user(user)
            except NotImplementedError:
                return _fail("registration_unavailable")
            created = True
            _audit(user.id, "REGISTER", {"email": address, "roles": [role_name], "method": "google"})

    mailbox = UserMailbox(user_id=user.id, tenant_id=user.tenant_id, address=address, google_sub=info.get("sub"),
                          refresh_token_enc=encrypt_token(refresh_token), scopes=granted, status="active", connected_at=datetime.utcnow())
    try:
        repo.save_mailbox(mailbox)
    except Exception:  # e.g. migration 0007 not applied yet; the account (if created) stays usable with a password reset by an admin
        log.exception("could not persist the connected mailbox for %s", user.id)
        return _fail("storage_failed")
    _audit(user.id, "MAILBOX_CONNECTED", {"address": address, "scopes": granted, "can_send": mailbox.can_send()})
    if not st.get("connect"):
        _audit(user.id, "LOGIN", {"email": user.email, "roles": [r.value for r in user.roles], "method": "google"})

    token, payload = issue_token(user.id)
    fragment = urlencode({
        "token": token,
        "expires_at": datetime.utcfromtimestamp(payload["exp"]).isoformat() + "Z",
        "next": st.get("next") or "/welcome",
        "new": "1" if created else "0",
        "mailbox": address,
    })
    return RedirectResponse(f"{frontend_url()}/auth/callback#{fragment}", status_code=302)
