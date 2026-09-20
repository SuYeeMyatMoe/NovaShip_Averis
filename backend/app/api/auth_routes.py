"""
Login / register / logout. Every outcome (including failed logins) is audited.

    POST /auth/register  {email, password, display_name, role?}  -> session
    POST /auth/login     {email, password}                        -> session
    POST /auth/logout                                             -> revokes the session
    GET  /auth/session                                            -> current user + permissions
    GET  /auth/config                                             -> what the register form may offer
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.auth.accounts import DEMO_PASSWORD, allowed_register_roles, decode_token, hash_password, issue_token, password_problem, verify_password
from app.auth.rbac import PERMISSIONS, current_user, has_permission
from app.config import auth_mode, env_bool, get_repo
from app.contracts.schemas import ActorType, AuditEvent, Role, UserRecord

router = APIRouter(prefix="/auth", tags=["auth"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    email: str
    password: str
    display_name: str = Field(min_length=2, max_length=80)
    role: Optional[str] = None


def _audit(actor_id: str, action: str, after: Optional[dict[str, Any]] = None) -> None:
    repo = get_repo()
    repo.append_audit(AuditEvent(event_id=f"evt_auth_{uuid4().hex}", case_id=None, timestamp=datetime.utcnow(),
                                 actor_type=ActorType.USER, actor_id=actor_id, action=action, after=after))


def _session_payload(user: UserRecord) -> dict[str, Any]:
    token, payload = issue_token(user.id)
    return {
        "token": token,
        "expires_at": datetime.utcfromtimestamp(payload["exp"]).isoformat() + "Z",
        "user": {**user.model_dump(mode="json"), "permissions": [p for p in PERMISSIONS if has_permission(user, p)]},
    }


def seed_demo_credentials() -> int:
    """Give every seeded user without a password the shared demo password (idempotent)."""
    if auth_mode() != "demo":
        return 0
    repo = get_repo()
    n = 0
    for u in repo.list_users():
        if not repo.get_password_hash(u.id):
            repo.set_password_hash(u.id, hash_password(DEMO_PASSWORD))
            n += 1
    return n


@router.get("/config")
def auth_config():
    """Register-form options. In demo mode also lists the seeded accounts so the login page can offer one-click fills."""
    mode = auth_mode()
    enabled = mode == "demo" or (mode == "local" and env_bool("SELF_REGISTRATION_ENABLED"))
    out: dict[str, Any] = {"register_roles": allowed_register_roles() if enabled else [], "min_password_length": 8, "auth_mode": mode, "registration_enabled": enabled}
    if mode == "demo":
        out["demo_password"] = DEMO_PASSWORD
        out["demo_accounts"] = [{"email": u.email, "display_name": u.display_name, "roles": [r.value for r in u.roles]}
                                for u in get_repo().list_users() if u.id.startswith(("u_ops_", "u_sup_", "u_admin_", "u_audit_"))]
    return out


@router.post("/login")
def login(req: LoginRequest):
    if auth_mode() == "jwt":
        raise HTTPException(403, detail={"error": "local login is disabled in AUTH_MODE=jwt", "category": "AUTH_ERROR"})
    repo = get_repo()
    email = req.email.strip().lower()
    user = repo.get_user_by_email(email)
    if auth_mode() == "demo" and user and repo.get_password_hash(user.id) is None:
        seed_demo_credentials()  # first login before startup seeding ran (tests / fresh Supabase)
    if not user or not verify_password(req.password, repo.get_password_hash(user.id)):
        _audit(user.id if user else email, "LOGIN_FAILED", {"email": email})
        from app.ai.operator_behaviour import login_failed_warning

        warn = login_failed_warning(repo, email)
        if warn:
            _audit(user.id if user else email, "UNUSUAL_OPERATOR_BEHAVIOUR", {"signal": warn.signal, "evidence": warn.evidence})
        raise HTTPException(401, detail={"error": "invalid email or password", "category": "AUTH_ERROR", "operator_warning": bool(warn)})
    _audit(user.id, "LOGIN", {"email": email, "roles": [r.value for r in user.roles]})
    return _session_payload(user)


@router.post("/register", status_code=201)
def register(req: RegisterRequest):
    mode = auth_mode()
    if mode == "jwt" or (mode == "local" and not env_bool("SELF_REGISTRATION_ENABLED")):
        raise HTTPException(403, detail={"error": "self-registration is disabled", "category": "AUTH_ERROR"})
    repo = get_repo()
    email = req.email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(400, detail={"error": "enter a valid email address", "category": "AUTH_ERROR"})
    if (problem := password_problem(req.password)):
        raise HTTPException(400, detail={"error": problem, "category": "AUTH_ERROR"})
    if repo.get_user_by_email(email):
        raise HTTPException(409, detail={"error": "an account with this email already exists - log in instead", "category": "AUTH_ERROR"})
    allowed = allowed_register_roles()
    role_name = (req.role or (allowed[0] if allowed else "OPERATIONS_STAFF")).upper()
    if role_name not in allowed:
        raise HTTPException(403, detail={"error": f"self-registration may only pick {', '.join(allowed) or 'no role'}; ask an ADMIN for other roles", "category": "AUTH_ERROR"})
    uid = "u_" + re.sub(r"[^a-z0-9]+", "_", email.split("@", 1)[0]).strip("_")[:24]
    base, i = uid, 2
    while repo.get_user(uid):
        uid, i = f"{base}_{i}", i + 1
    user = UserRecord(id=uid, email=email, display_name=req.display_name.strip(), roles=[Role(role_name)])
    try:
        repo.save_user(user)
    except NotImplementedError:
        raise HTTPException(501, detail={"error": "registration is not available on this backend", "category": "AUTH_ERROR"})
    repo.set_password_hash(user.id, hash_password(req.password))
    _audit(user.id, "REGISTER", {"email": email, "roles": [role_name]})
    _audit(user.id, "LOGIN", {"email": email, "roles": [role_name]})
    return _session_payload(user)


@router.post("/logout")
def logout(user: UserRecord = Depends(current_user), authorization: Optional[str] = Header(default=None)):
    token = authorization.split(" ", 1)[1].strip() if authorization and " " in authorization else ""
    payload = decode_token(token) or {}
    if payload.get("sid"):
        get_repo().revoke_session(payload["sid"])
    _audit(user.id, "LOGOUT", {"session_revoked": bool(payload.get("sid"))})
    return {"ok": True}


@router.get("/session")
def session(user: UserRecord = Depends(current_user)):
    return {**user.model_dump(mode="json"), "permissions": [p for p in PERMISSIONS if has_permission(user, p)]}
