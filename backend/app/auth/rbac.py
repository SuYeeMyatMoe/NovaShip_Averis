"""
Least-privilege RBAC.

Identity comes from (in order):
  1. In demo/local mode, a signed `nsa.<...>` session issued by POST /auth/login
     (see auth/accounts.py; revoked by POST /auth/logout)
  2. In jwt mode, a Supabase JWT verified against the project's JWKS
  3. `X-User-Id` header - AUTH_MODE=demo only (tests, curl, scripts)

There is no silent default user: a request without credentials is 401 so the
UI can send the person to the login page.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request

from app.auth.accounts import decode_token, is_session_token
from app.config import auth_mode, get_repo
from app.contracts.schemas import Role, UserRecord

PERMISSIONS: dict[str, set[Role]] = {
    "view_case": {Role.OPERATIONS_STAFF, Role.SUPERVISOR, Role.ADMIN, Role.AUDITOR},
    "mutate_case": {Role.OPERATIONS_STAFF, Role.SUPERVISOR, Role.ADMIN},
    "view_document": {Role.OPERATIONS_STAFF, Role.SUPERVISOR, Role.ADMIN, Role.AUDITOR},
    "compare": {Role.OPERATIONS_STAFF, Role.SUPERVISOR, Role.ADMIN},
    "edit_extraction": {Role.OPERATIONS_STAFF, Role.SUPERVISOR, Role.ADMIN},
    "generate_draft": {Role.OPERATIONS_STAFF, Role.SUPERVISOR, Role.ADMIN},
    "approve_send": {Role.SUPERVISOR, Role.ADMIN},
    "share_internal": {Role.OPERATIONS_STAFF, Role.SUPERVISOR, Role.ADMIN},
    "notify_external": {Role.SUPERVISOR, Role.ADMIN},
    "assign": {Role.OPERATIONS_STAFF, Role.SUPERVISOR, Role.ADMIN},
    "view_policy": {Role.OPERATIONS_STAFF, Role.SUPERVISOR, Role.ADMIN},
    "edit_policy": {Role.ADMIN},
    "view_audit": {Role.SUPERVISOR, Role.ADMIN, Role.AUDITOR},
    "export_data": {Role.SUPERVISOR, Role.ADMIN, Role.AUDITOR},
    "batch": {Role.SUPERVISOR, Role.ADMIN},
    "ingest": {Role.ADMIN, Role.SUPERVISOR, Role.OPERATIONS_STAFF},
}

AUTH_MODE = os.environ.get("AUTH_MODE", "demo").lower()  # compatibility export; runtime checks use auth_mode()


def has_permission(user: UserRecord, perm: str) -> bool:
    return any(r in PERMISSIONS.get(perm, set()) for r in user.roles)


def _from_session(token: str) -> Optional[UserRecord]:
    payload = decode_token(token)
    if not payload:
        return None
    repo = get_repo()
    if repo.is_session_revoked(payload.get("sid", "")):
        return None
    return repo.get_user(payload.get("sub", ""))


@lru_cache(maxsize=4)
def _jwks_client(url: str):
    import jwt

    return jwt.PyJWKClient(url, cache_keys=True)


def _jwt_claims(token: str) -> dict:
    import jwt

    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    if not url:
        raise jwt.InvalidTokenError("SUPABASE_URL is not configured")
    issuer = os.environ.get("SUPABASE_JWT_ISSUER", f"{url}/auth/v1")
    audience = os.environ.get("SUPABASE_JWT_AUDIENCE", "authenticated")
    algorithm = os.environ.get("SUPABASE_JWT_ALGORITHM", "JWKS").upper()
    options = {"require": ["exp", "iat", "sub", "aud", "iss"]}
    if algorithm == "HS256":
        secret = os.environ.get("SUPABASE_JWT_SECRET", "")
        if not secret:
            raise jwt.InvalidTokenError("legacy HS256 verification requires SUPABASE_JWT_SECRET")
        return jwt.decode(token, secret, algorithms=["HS256"], audience=audience, issuer=issuer, options=options)
    if algorithm != "JWKS":
        raise jwt.InvalidTokenError("SUPABASE_JWT_ALGORITHM must be JWKS or HS256")
    key = _jwks_client(f"{url}/auth/v1/.well-known/jwks.json").get_signing_key_from_jwt(token)
    return jwt.decode(token, key.key, algorithms=["RS256", "ES256"], audience=audience, issuer=issuer, options=options)


def _from_jwt(token: str) -> Optional[UserRecord]:
    try:
        claims = _jwt_claims(token)
    except Exception:
        return None
    repo = get_repo()
    uid = claims.get("sub")
    if not uid:
        return None
    try:
        UUID(uid)
    except (TypeError, ValueError):
        return None
    return repo.get_user_by_auth_subject(uid)


def current_user(request: Request, authorization: Optional[str] = Header(default=None), x_user_id: Optional[str] = Header(default=None)) -> UserRecord:
    repo = get_repo()
    mode = auth_mode()
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        if is_session_token(token):
            user = _from_session(token) if mode in {"demo", "local"} else None
        else:
            user = _from_jwt(token) if mode == "jwt" else None
        if user:
            return user
        raise HTTPException(401, detail={"error": "session expired or invalid - please log in again", "category": "AUTH_ERROR"})
    if mode == "demo" and x_user_id:
        user = repo.get_user(x_user_id)
        if user:
            return user
        raise HTTPException(401, detail={"error": f"unknown demo user '{x_user_id}'", "category": "AUTH_ERROR"})
    raise HTTPException(401, detail={"error": "authentication required - log in at /auth/login", "category": "AUTH_ERROR"})


def require(perm: str):
    def _dep(user: UserRecord = Depends(current_user)) -> UserRecord:
        if not has_permission(user, perm):
            raise HTTPException(403, detail={"error": f"role(s) {[r.value for r in user.roles]} lack permission '{perm}'", "category": "AUTH_ERROR"})
        return user

    return _dep
