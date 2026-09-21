"""REST API. Every mutation writes an audit event (via CaseService / Pipeline)."""
from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse, Response

from app.ai.assistant import answer_question, translate_text
from app.auth.rbac import current_user, has_permission, require
from app.config import BUNDLE_DIR, get_repo
from app.contracts.schemas import (
    SEVEN_FIELDS,
    ActorType,
    AskRequest,
    AskResponse,
    AssignRequest,
    BatchRequest,
    CaseStatus,
    DraftDecision,
    DraftRequest,
    FieldResult,
    Role,
    ShareRequest,
    UserRecord,
)
from app.core.policy import explain_policy, merged_policy
from app.file_security import UnsafeUpload, max_file_bytes, resolve_bundle_attachment, safe_filename, validate_attachment_count, validate_file_size
from app.services.case_service import CaseService
from app.services.submission import case_to_submission_row

router = APIRouter()


_ATTENTION_STATUSES = {
    CaseStatus.HUMAN_REVIEW,
    CaseStatus.WAITING_DOCUMENTS,
    CaseStatus.SECURITY_REVIEW,
    CaseStatus.DRAFT_READY,
    CaseStatus.MISMATCH_DETECTED,
}
_IDLE_STATUSES = {CaseStatus.COMPLETED, CaseStatus.NO_ACTION_INFO}
_STATUS_REASONS = {
    CaseStatus.HUMAN_REVIEW: "Human review",
    CaseStatus.WAITING_DOCUMENTS: "Waiting for documents",
    CaseStatus.SECURITY_REVIEW: "Security review",
    CaseStatus.DRAFT_READY: "Draft ready to approve",
    CaseStatus.MISMATCH_DETECTED: "Mismatch detected",
}


def svc() -> CaseService:
    return CaseService(get_repo())


def _needs_human(case) -> bool:
    if case.status in _ATTENTION_STATUSES:
        return True
    return bool(case.action_required and case.status not in _IDLE_STATUSES)


def _notification_reason(case, user_id: str) -> str:
    parts = []
    if case.assigned_user_id == user_id:
        parts.append("Assigned to you")
    parts.append(_STATUS_REASONS.get(case.status) or ("Action required" if case.action_required else "Update"))
    return " · ".join(parts)


def _email_view(email) -> dict[str, Any] | None:
    if not email:
        return None
    d = email.model_dump(mode="json")
    for attachment in d.get("attachments") or []:
        attachment["raw_text"] = None
    return d


def _case_view(case, email) -> dict[str, Any]:
    d = case.model_dump(mode="json")
    d["email"] = _email_view(email)
    return d


def _mutation_view(s: CaseService, case) -> dict[str, Any]:
    """Case view plus the operator signal raised by this call (the UI opens a warning dialog on it)."""
    d = _case_view(case, s.repo.get_email(case.source_email_id))
    d["operator_warning"] = s.pop_operator_warning()
    return d


def _case_row(c, e) -> dict[str, Any]:
    return {
        "id": c.id, "email_id": c.source_email_id, "subject": e.subject if e else "", "sender": e.sender if e else "", "received_at": e.received_at.isoformat() if e else None,
        "intent": c.intent.value, "category": c.hackathon_category.value, "security": c.security.outcome.value, "action_required": c.action_required,
        "priority": c.priority.value, "si_available": c.si_available, "bl_available": c.bl_available, "attachments": len(e.attachments) if e else 0,
        "mismatch_count": c.mismatch_count, "comparison_status": c.comparison_status.value if c.comparison_status else None, "review_reason": c.review_reason.value if c.review_reason else None,
        "confidence": c.confidence, "assigned_user_id": c.assigned_user_id, "shared_with": c.shared_with, "status": c.status.value, "updated_at": c.updated_at.isoformat(),
        "summary": c.summary.text if c.summary else "", "errors": len([x for x in c.errors if not x.resolved]), "drafts": len(c.drafts),
        "mailbox_user_id": e.mailbox_user_id if e else None, "mailbox": e.mailbox_address if e else None,
        "agent_run": c.agent_run.summary() if c.agent_run else None,
    }


def _field_flagged(c, field: str) -> bool:
    """True when the seven-field comparison flagged `field` (mismatch, missing on one side, or low-confidence review)."""
    if not c.comparison:
        return False
    return any(f.field == field and f.result != FieldResult.MATCH for f in c.comparison.fields)


def _agent_state(c) -> str:
    """pending (never run or last run errored) | paused | done. A case a person marked complete is done regardless of the agent."""
    if c.status == CaseStatus.COMPLETED:
        return "done"
    if c.agent_run is None or c.agent_run.result == "error":
        return "pending"
    return "paused" if c.agent_run.result == "paused" else "done"


from app.services.reporting import dashboard_metrics as _dashboard_metrics  # noqa: E402  (shared with the Excel report)


# ---------------------------------------------------------------- health
@router.get("/health")
def health():
    repo = get_repo()
    counts = repo.cached_counts() if hasattr(repo, "cached_counts") else {
        "cases": len(repo.list_cases()),
        "emails": len(repo.list_emails()),
    }
    return {"status": "ok", "backend": type(repo).__name__, **counts, "time": datetime.utcnow().isoformat(), "llm": llm_posture(), "migrations": migration_posture(repo)}


def migration_posture(repo) -> dict[str, Any]:
    """Which optional schema pieces the persistence layer has; the UI turns a missing one into a banner instead of a silent loop."""
    try:
        ready = bool(repo.mailbox_storage_ready())
    except Exception:
        ready = False
    return {"user_mailboxes": ready, "apply_with": "python backend/scripts/apply_migrations.py"}


def llm_posture() -> dict[str, Any]:
    """What leaves the desk towards a model provider right now (no secrets): provider, model, masking, OCR, call counters."""
    from app.ai.llm import get_llm, llm_call_stats
    from app.ai.privacy import privacy_settings
    from app.readers.document_reader import ocr_enabled

    llm = get_llm()
    settings = privacy_settings()
    return {
        "provider": llm.provider if llm.enabled else "none",
        "model": llm.model if llm.enabled else None,
        "privacy": "mask" if settings["mask_identifiers"] else "off",
        "audit_provider_calls": settings["audit_provider_calls"],
        "vision_ocr": bool(ocr_enabled() and settings["allow_vision_ocr"] and os.environ.get("GOOGLE_API_KEY")),
        "embeddings": os.environ.get("EMBEDDING_PROVIDER", "local"),
        "calls": llm_call_stats(),
    }


def mailbox_providers() -> dict[str, Any]:
    """Which mailbox paths this deployment offers (for the login/register pages and the mailbox card)."""
    from app.api.google_auth_routes import google_sign_in_enabled
    from app.api.microsoft_auth_routes import microsoft_sign_in_enabled
    from app.connectors.email_connectors import shared_mailbox_configured

    try:
        storage_ready = bool(get_repo().mailbox_storage_ready())
    except Exception:
        storage_ready = False
    return {"google": google_sign_in_enabled(), "microsoft": microsoft_sign_in_enabled(), "shared_mailbox_configured": shared_mailbox_configured(),
            "mailbox_storage_ready": storage_ready}


def mailbox_summary(user_id: str) -> dict[str, Any]:
    """Safe view of a user's connected mailbox for /me, /auth/session and the shell; never includes the token."""
    mailbox = get_repo().get_mailbox(user_id)
    return {**(mailbox.public() if mailbox else {"connected": False}), "providers": mailbox_providers()}


@router.get("/me")
def me(user: UserRecord = Depends(current_user)):
    from app.auth.rbac import PERMISSIONS

    return {**user.model_dump(mode="json"), "permissions": [p for p in PERMISSIONS if has_permission(user, p)], "mailbox": mailbox_summary(user.id)}


@router.get("/me/operator-profile")
def my_operator_profile(user: UserRecord = Depends(current_user)):
    """What the operator guard has learned for the caller from the audit log, and the limits that apply now."""
    from app.ai.operator_behaviour import operator_baseline

    s = svc()
    settings = s.guard()
    return {"settings": settings, "baseline": operator_baseline(s.repo, user.id, settings)}


@router.get("/me/mailbox")
def my_mailbox(user: UserRecord = Depends(current_user)):
    return mailbox_summary(user.id)


@router.delete("/me/mailbox")
def disconnect_mailbox(user: UserRecord = Depends(current_user)):
    """Forget the connected mailbox (Gmail or Outlook): best-effort revoke at Google, then delete the encrypted token."""
    repo = get_repo()
    mailbox = repo.get_mailbox(user.id)
    if not mailbox:
        raise HTTPException(404, detail={"error": "no mailbox is connected to this account", "category": "EMAIL_CONNECTOR_ERROR"})
    revoked = False
    if mailbox.provider == "gmail":
        try:
            from app.auth.mailbox_tokens import decrypt_token
            import httpx

            httpx.post("https://oauth2.googleapis.com/revoke", data={"token": decrypt_token(mailbox.refresh_token_enc)}, timeout=10)
            revoked = True
        except Exception:  # the local record is removed regardless; the user can also revoke at myaccount.google.com
            revoked = False
    repo.delete_mailbox(user.id)
    svc().pipe.audit(None, ActorType.USER, user.id, "MAILBOX_DISCONNECTED", after={"address": mailbox.address, "provider": mailbox.provider, "provider_revoked": revoked})
    note = None if mailbox.provider == "gmail" else "Microsoft has no revoke endpoint for refresh tokens; remove NovaShip at myaccount.microsoft.com/consent if you want the grant gone too."
    return {"ok": True, "address": mailbox.address, "provider": mailbox.provider, "google_revoked": revoked, "note": note}


@router.get("/me/notifications")
def my_notifications(user: UserRecord = Depends(require("view_case")), limit: int = 20):
    """In-app queue of cases that need a person. Admin/Supervisor see the shared desk; everyone also sees work assigned to them."""
    repo = get_repo()
    desk_wide = any(r in {Role.ADMIN, Role.SUPERVISOR} for r in user.roles)
    candidates = []
    for case in repo.list_cases():
        mine = case.assigned_user_id == user.id
        if not (mine or (desk_wide and _needs_human(case))):
            continue
        if mine and case.status in _IDLE_STATUSES and not _needs_human(case):
            continue
        candidates.append(case)
    candidates.sort(key=lambda c: c.updated_at, reverse=True)
    total = len(candidates)
    cap = max(1, min(limit, 50))
    items = [{
        "kind": "needs_person",
        "case_id": case.id,
        "subject": (case.summary.text if case.summary and case.summary.text else case.id),
        "status": case.status.value,
        "priority": case.priority.value,
        "reason": _notification_reason(case, user.id),
        "updated_at": case.updated_at.isoformat(),
    } for case in candidates[:cap]]
    new_mail = _new_mail_notifications(repo, user.id, cap)
    shared_with_me = _shared_with_me_notifications(repo, user.id, cap)
    return {"items": items, "total": total, "new_mail": new_mail, "new_mail_total": len(new_mail), "shared": shared_with_me, "shared_total": len(shared_with_me)}


SHARED_WINDOW_D = 7


def _shared_with_me_notifications(repo, user_id: str, cap: int) -> list[dict[str, Any]]:
    """Cases a colleague shared with the caller in the last 7 days (delivered internal shares), newest first."""
    since = datetime.utcnow() - timedelta(days=SHARED_WINDOW_D)
    names = {u.id: u.display_name for u in repo.list_users()}
    out: list[dict[str, Any]] = []
    shares = [s for s in repo.list_shares() if s.recipient_user_id == user_id and s.status in {"SENT", "SIMULATED", "VIEWED"} and s.sent_at and s.sent_at >= since]
    shares.sort(key=lambda s: s.sent_at or datetime.min, reverse=True)
    for share in shares:
        case = repo.get_case(share.case_id)
        if not case:
            continue
        email = repo.get_email(case.source_email_id)
        out.append({
            "kind": "shared", "share_id": share.id, "case_id": case.id, "shared_by": share.shared_by, "shared_by_name": names.get(share.shared_by, share.shared_by),
            "subject": (email.subject if email and email.subject else case.id), "message": (share.message or "")[:160], "status": case.status.value,
            "priority": case.priority.value, "shared_at": share.sent_at.isoformat(),
        })
        if len(out) >= cap:
            break
    return out


NEW_MAIL_WINDOW_H = 48


def _new_mail_notifications(repo, user_id: str, cap: int) -> list[dict[str, Any]]:
    """Cases that arrived through the caller's own connected mailbox (Outlook/Gmail) in the last 48 h, newest first.
    The bell shows them as 'New mail'; the browser remembers which ones were closed."""
    since = datetime.utcnow() - timedelta(hours=NEW_MAIL_WINDOW_H)
    recent = sorted((c for c in repo.list_cases() if c.created_at >= since), key=lambda c: c.created_at, reverse=True)
    out: list[dict[str, Any]] = []
    for case in recent:
        email = repo.get_email(case.source_email_id)
        if not email or email.mailbox_user_id != user_id:
            continue
        out.append({
            "kind": "new_mail", "case_id": case.id, "email_id": email.id, "mailbox": email.mailbox_address,
            "subject": email.subject or case.id, "sender": email.sender, "status": case.status.value, "priority": case.priority.value,
            "action_required": bool(case.action_required), "received_at": email.received_at.isoformat(), "created_at": case.created_at.isoformat(),
        })
        if len(out) >= cap:
            break
    return out


@router.get("/users")
def users(user: UserRecord = Depends(require("view_case"))):
    repo = get_repo()
    return {"users": [u.model_dump(mode="json") for u in repo.list_users()], "teams": getattr(repo, "teams", []), "parties": [p.model_dump(mode="json") for p in repo.list_parties()]}


# ---------------------------------------------------------------- ingestion
@router.post("/webhooks/email", status_code=201)
def webhook_email(payload: dict[str, Any], user: UserRecord = Depends(require("ingest"))):
    """Email connector webhook. Body: {email_id?, from, to?, cc?, subject, body, attachments: [{name, content_base64}] | [paths]}.
    Idempotent on message content (duplicate -> returns the existing case)."""
    s = svc()
    atts_in = payload.get("attachments", []) or []
    try:
        validate_attachment_count(len(atts_in))
    except UnsafeUpload as exc:
        raise HTTPException(400, detail={"error": str(exc), "category": "ATTACHMENT_UPLOAD_ERROR"})
    blobs: dict[str, bytes] = {}
    paths: list[str] = []
    for a in atts_in:
        if isinstance(a, dict):
            try:
                name = safe_filename(a.get("name", "attachment.bin"))
            except UnsafeUpload as exc:
                raise HTTPException(400, detail={"error": str(exc), "category": "ATTACHMENT_UPLOAD_ERROR"})
            path = f"attachments/{name}"
            encoded = a.get("content_base64", "")
            if encoded and len(encoded) > ((max_file_bytes() + 2) // 3) * 4 + 4:
                raise HTTPException(413, detail={"error": "attachment exceeds maximum size", "category": "ATTACHMENT_UPLOAD_ERROR"})
            try:
                blobs[path] = base64.b64decode(encoded, validate=True) if encoded else b""
                validate_file_size(len(blobs[path]))
            except UnsafeUpload as exc:
                raise HTTPException(413, detail={"error": str(exc), "category": "ATTACHMENT_UPLOAD_ERROR"})
            except (ValueError, TypeError) as exc:
                raise HTTPException(400, detail={"error": "attachment content_base64 is invalid", "category": "ATTACHMENT_UPLOAD_ERROR"}) from exc
            paths.append(path)
        else:
            p = str(a)
            try:
                f = resolve_bundle_attachment(BUNDLE_DIR, p)
            except UnsafeUpload as exc:
                raise HTTPException(400, detail={"error": str(exc), "category": "ATTACHMENT_UPLOAD_ERROR"})
            blobs[p] = f.read_bytes() if f.exists() else b""
            try:
                validate_file_size(len(blobs[p]))
            except UnsafeUpload as exc:
                raise HTTPException(413, detail={"error": str(exc), "category": "ATTACHMENT_UPLOAD_ERROR"})
            paths.append(p)
    raw = {**payload, "attachments": paths}
    email = s.pipe.ingest_email(raw, blobs, provider=payload.get("provider", "webhook"))
    if email.is_duplicate_of:
        existing = s.repo.get_case_by_email(email.is_duplicate_of)
        if existing:
            return {"duplicate_of": email.is_duplicate_of, "case": _case_view(existing, s.repo.get_email(existing.source_email_id))}
    case = s.pipe.run(email, actor_id=user.id)
    return _case_view(case, email)


@router.post("/connectors/poll")
def connectors_poll(limit: int = 25, source: str = Query(default="auto", description="auto | mine | shared"), user: UserRecord = Depends(require("ingest"))):
    """Poll a mailbox and run the pipeline idempotently.

    `mine` = the caller's connected Gmail/Outlook; `shared` = the optional desk mailbox from .env (EMAIL_PROVIDER);
    `auto` = the caller's mailbox when connected, otherwise the shared one.
    """
    from app.connectors.email_connectors import get_connector
    from app.services.mailbox_service import MailboxPollError, poll_connector, poll_user_mailbox

    s = svc()
    source = (source or "auto").strip().lower()
    if source not in {"auto", "mine", "shared"}:
        raise HTTPException(400, detail={"error": "source must be auto, mine or shared", "category": "EMAIL_CONNECTOR_ERROR", "retryable": False})
    mailbox = s.repo.get_mailbox(user.id) if source != "shared" else None
    if source == "mine" and mailbox is None:
        raise HTTPException(400, detail={"error": "no mailbox is connected to this account - connect Outlook or Gmail from the Guide page first", "category": "EMAIL_CONNECTOR_ERROR", "recovery": "Connect Gmail from the Guide page", "retryable": False})
    try:
        if mailbox is not None and mailbox.status != "revoked":
            return poll_user_mailbox(s, mailbox, actor_id=user.id, limit=limit)
        conn = get_connector()
        if conn is None:
            if source == "shared":
                raise HTTPException(400, detail={"error": "no shared mailbox is configured (EMAIL_PROVIDER is 'none')", "category": "EMAIL_CONNECTOR_ERROR", "code": "NO_SHARED_MAILBOX", "recovery": "Connect your own Gmail or Outlook from the Guide page", "retryable": False})
            raise HTTPException(400, detail={"error": "no mailbox is connected to this account and the desk has no shared mailbox", "category": "EMAIL_CONNECTOR_ERROR", "code": "NO_MAILBOX_CONNECTED", "recovery": "Connect Gmail or Outlook from the Guide page, then fetch again", "retryable": False})
        return poll_connector(s, conn, actor_id=user.id, limit=limit)
    except MailboxPollError as exc:
        raise HTTPException(502, detail={"error": str(exc), "category": "EMAIL_CONNECTOR_ERROR", "recovery": "Check credentials / network and retry", "retryable": True})


@router.post("/ingest/bundle")
def ingest_bundle(limit: int = 0, user: UserRecord = Depends(require("ingest"))):
    """Import the local SDOC bundle (demo fixture path)."""
    s = svc()
    files = sorted((BUNDLE_DIR / "inbox").glob("email_*.json"))
    if limit:
        files = files[:limit]
    n = 0
    for p in files:
        raw = json.loads(p.read_text(encoding="utf-8"))
        blobs = {}
        for attachment_path in raw.get("attachments", []):
            candidate = resolve_bundle_attachment(BUNDLE_DIR, attachment_path)
            if candidate.exists():
                validate_file_size(candidate.stat().st_size)
                blobs[attachment_path] = candidate.read_bytes()
        email = s.pipe.ingest_email(raw, blobs)
        s.pipe.run(email, actor_id=user.id)
        n += 1
    return {"ingested": n}


# ---------------------------------------------------------------- dashboard
@router.get("/dashboard/metrics")
def dashboard_metrics(user: UserRecord = Depends(require("view_case"))):
    repo = get_repo()
    return _dashboard_metrics(repo.list_cases(), len(repo.list_emails()))


@router.get("/dashboard/bootstrap")
def dashboard_bootstrap(user: UserRecord = Depends(require("view_case"))):
    """One payload for the inbox widgets so the page does not wait on 6 separate list scans."""
    from app.api.agent_routes import _field_stats, _security_rows

    repo = get_repo()
    cases = repo.list_cases()
    emails = {e.id: e for e in repo.list_emails()}
    idle = {"COMPLETED", "NO_ACTION_INFO", "AWAITING_RESPONSE"}
    rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    attention = [_case_row(c, emails.get(c.source_email_id)) for c in cases if c.action_required and c.status.value not in idle]
    attention.sort(key=lambda r: (rank.get(r["priority"], 9), r["updated_at"]), reverse=False)
    activity = None
    if has_permission(user, "view_audit"):
        events = repo.list_audit()
        events = list(reversed(events[-8:]))
        activity = [a.model_dump(mode="json") for a in events]
    return {
        "metrics": _dashboard_metrics(cases, len(emails)),
        "fields": _field_stats(cases),
        "attention": attention[:7],
        "security": [{"outcome": row["outcome"]} for row in _security_rows(cases, emails)],
        "activity": activity,
        "users": [u.model_dump(mode="json") for u in repo.list_users()],
    }


# ---------------------------------------------------------------- cases
@router.get("/cases")
def list_cases(
    status: Optional[str] = None, priority: Optional[str] = None, intent: Optional[str] = None, category: Optional[str] = None,
    mismatch: Optional[str] = Query(default=None, description="yes|no"), assigned: Optional[str] = None, shared: Optional[str] = None,
    field: Optional[str] = Query(default=None, description="one of the seven fields: keep cases whose comparison of that field is not a MATCH"),
    sender: Optional[str] = None, q: Optional[str] = None, min_confidence: Optional[float] = None, security: Optional[str] = None,
    date_from: Optional[str] = None, date_to: Optional[str] = None, attention: Optional[str] = Query(default=None, description="yes to restrict to human-needed cases"),
    mailbox: Optional[str] = Query(default=None, description="user id of a connected mailbox, 'me', or 'shared'"),
    agent: Optional[str] = Query(default=None, description="pending (not run by the AI agent yet) | paused | done | any"),
    limit: int = 100, offset: int = 0, sort: str = "updated_desc",
    user: UserRecord = Depends(require("view_case")),
):
    if mailbox == "me":
        mailbox = user.id
    agent = (agent or "any").lower()
    repo = get_repo()
    emails = {e.id: e for e in repo.list_emails()}
    rows = []
    for c in repo.list_cases():
        e = emails.get(c.source_email_id)
        if agent in ("pending", "paused", "done") and _agent_state(c) != agent:
            continue
        if status and c.status.value != status:
            continue
        if priority and c.priority.value != priority:
            continue
        if intent and c.intent.value != intent:
            continue
        if category and c.hackathon_category.value != category:
            continue
        if security and c.security.outcome.value != security:
            continue
        if mismatch == "yes" and c.mismatch_count == 0:
            continue
        if mismatch == "no" and (c.mismatch_count > 0 or not c.comparison):
            continue
        if field and not _field_flagged(c, field):
            continue
        if assigned and c.assigned_user_id != assigned:
            continue
        if attention and attention.lower() in {"yes", "1", "true"} and not _needs_human(c):
            continue
        if shared and shared not in c.shared_with:
            continue
        if sender and (not e or sender.lower() not in e.sender.lower()):
            continue
        if mailbox == "shared" and e and e.mailbox_user_id:
            continue
        if mailbox and mailbox != "shared" and (not e or e.mailbox_user_id != mailbox):
            continue
        if min_confidence is not None and c.confidence < min_confidence:
            continue
        if date_from and e and e.received_at.isoformat() < date_from:
            continue
        if date_to and e and e.received_at.isoformat() > date_to:
            continue
        if q:
            hay = f"{c.id} {e.subject if e else ''} {e.sender if e else ''} {c.summary.text if c.summary else ''}".lower()
            if q.lower() not in hay:
                continue
        rows.append(_case_row(c, e))
    key = {"updated_desc": (lambda r: r["updated_at"], True), "received_desc": (lambda r: r["received_at"] or "", True), "priority": (lambda r: {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}[r["priority"]], False), "confidence": (lambda r: r["confidence"], False),
           "run_desc": (lambda r: (r["agent_run"] or {}).get("last_run_at") or "", True)}.get(sort, (lambda r: r["updated_at"], True))
    rows.sort(key=key[0], reverse=key[1])
    return {"total": len(rows), "items": rows[offset: offset + limit]}


@router.get("/cases/suggest")
def suggest_cases(q: str = "", limit: int = 10, user: UserRecord = Depends(require("view_case"))):
    """Autocomplete for case pickers: substring match on case id, subject and sender; id prefix matches rank first."""
    repo = get_repo()
    needle = (q or "").strip().lower()
    emails = {e.id: e for e in repo.list_emails()}
    cap = max(1, min(int(limit or 10), 50))
    scored: list[tuple[int, str, dict[str, Any]]] = []
    for c in repo.list_cases():
        e = emails.get(c.source_email_id)
        cid = c.id.lower()
        subject = (e.subject if e else "").lower()
        sender = (e.sender if e else "").lower()
        if needle:
            if cid.startswith(needle) or cid.replace("case_", "").startswith(needle):
                rank = 0
            elif needle in cid:
                rank = 1
            elif needle in subject or needle in sender:
                rank = 2
            else:
                continue
        else:
            rank = 3
        scored.append((rank, c.updated_at.isoformat(), {"id": c.id, "subject": e.subject if e else "", "sender": e.sender if e else "", "status": c.status.value,
                                                        "priority": c.priority.value, "mismatch_count": c.mismatch_count, "mailbox": e.mailbox_address if e else None,
                                                        "agent": _agent_state(c)}))
    ranked = sorted(scored, key=lambda t: t[1], reverse=True)          # newest first ...
    ranked.sort(key=lambda t: t[0])                                       # ... within rank (stable)
    return {"items": [row for _rank, _ts, row in ranked[:cap]], "total": len(ranked)}


@router.get("/cases/{case_id}")
def get_case(case_id: str, user: UserRecord = Depends(require("view_case"))):
    s = svc()
    case = s.get(case_id)
    return _mutation_view(s, case)


@router.get("/cases/{case_id}/comparison")
def get_comparison(case_id: str, user: UserRecord = Depends(require("view_case"))):
    case = svc().get(case_id)
    if not case.comparison:
        raise HTTPException(404, detail={"error": "no comparison for this case", "category": "COMPARISON_ERROR", "recovery": "Upload SI + Draft BL and Retry", "retryable": True, "review_reason": case.review_reason.value if case.review_reason else None})
    return case.comparison.model_dump(mode="json")


@router.get("/cases/{case_id}/report")
def get_report(case_id: str, user: UserRecord = Depends(require("view_case"))):
    """Discrepancy report (spec section 8)."""
    s = svc()
    case = s.get(case_id)
    e = s.repo.get_email(case.source_email_id)
    si = next((a for a in e.attachments if a.id == case.si_document_id), None) if e else None
    bl = next((a for a in e.attachments if a.id == case.bl_document_id), None) if e else None
    cmp = case.comparison
    return {
        "case_id": case.id, "email_id": e.id, "sender": e.sender, "subject": e.subject, "received_at": e.received_at.isoformat(),
        "si_file": si.file_name if si else None, "bl_file": bl.file_name if bl else None, "compared_at": cmp.compared_at.isoformat() if cmp else None,
        "comparison": cmp.model_dump(mode="json") if cmp else None, "mismatch_count": case.mismatch_count, "mismatch_fields": cmp.mismatch_fields if cmp else [],
        "status": case.status.value, "review_reason": case.review_reason.value if case.review_reason else None, "recommendation": case.recommendation.model_dump(mode="json") if case.recommendation else None,
        "summary": case.summary.text if case.summary else None, "assigned_user_id": case.assigned_user_id, "assigned_team_id": case.assigned_team_id, "shared_with": case.shared_with,
        "audit_link": f"/cases/{case.id}/audit", "compact": _compact(cmp),
    }


def _compact(cmp) -> str:
    if not cmp:
        return "No comparison available."
    if cmp.mismatch_count == 0 and not cmp.review_fields:
        return cmp.message
    lines = [f"{cmp.mismatch_count} mismatch detected" if cmp.mismatch_count == 1 else f"{cmp.mismatch_count} mismatches detected"]
    for f in cmp.fields:
        if f.result.value == "MISMATCH":
            lines += [f"{f.label} - Mismatch", f"SI: {f.si_original}", f"BL: {f.bl_original}", f"Required attention: {f.attention}"]
    for f in cmp.fields:
        if f.result.value not in ("MATCH", "MISMATCH"):
            lines += [f"{f.label} - {f.result.value}", f"Required attention: {f.attention}"]
    return "\n".join(lines)


@router.get("/cases/{case_id}/audit")
def get_audit(case_id: str, user: UserRecord = Depends(require("view_case"))):
    repo = get_repo()
    return {"events": [a.model_dump(mode="json") for a in repo.list_audit(case_id)], "shares": [s.model_dump(mode="json") for s in repo.list_shares(case_id)]}


@router.get("/cases/{case_id}/documents/{attachment_id}")
def get_document(case_id: str, attachment_id: str, user: UserRecord = Depends(require("view_document"))):
    s = svc()
    case = s.get(case_id)
    e = s.repo.get_email(case.source_email_id)
    a = next((a for a in e.attachments if a.id == attachment_id), None)
    if not a:
        raise HTTPException(404, detail={"error": "attachment not found", "category": "ATTACHMENT_DOWNLOAD_ERROR"})
    signed = getattr(s.repo, "signed_url", lambda *_: None)(a.storage_pointer, 300) if a.storage_pointer else None
    return {**a.model_dump(mode="json"), "signed_url": signed, "expires_in_s": 300 if signed else None}


@router.get("/cases/{case_id}/documents/{attachment_id}/raw")
def get_document_raw(case_id: str, attachment_id: str, user: UserRecord = Depends(require("view_document"))):
    s = svc()
    case = s.get(case_id)
    e = s.repo.get_email(case.source_email_id)
    a = next((a for a in e.attachments if a.id == attachment_id), None)
    if not a:
        raise HTTPException(404, detail={"error": "attachment not found", "category": "ATTACHMENT_DOWNLOAD_ERROR"})
    data = s.repo.get_blob(a.storage_pointer) if a.storage_pointer else None
    if data is None:
        try:
            f = resolve_bundle_attachment(BUNDLE_DIR, f"attachments/{safe_filename(a.file_name)}")
        except UnsafeUpload as exc:
            raise HTTPException(400, detail={"error": str(exc), "category": "ATTACHMENT_DOWNLOAD_ERROR"})
        data = f.read_bytes() if f.exists() else None
    if data is None:
        raise HTTPException(404, detail={"error": "the original file is not stored for this attachment (storage bucket unavailable when it arrived); the extracted text is still available",
                                         "category": "ATTACHMENT_DOWNLOAD_ERROR", "recovery": "Check SUPABASE_STORAGE_BUCKET and /health.storage, then fetch the mail again"})
    media = {"pdf": "application/pdf", "txt": "text/plain; charset=utf-8", "md": "text/plain; charset=utf-8", "csv": "text/csv; charset=utf-8", "tsv": "text/tab-separated-values; charset=utf-8",
             "html": "text/plain; charset=utf-8", "htm": "text/plain; charset=utf-8", "eml": "message/rfc822", "rtf": "application/rtf",
             "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
             "doc": "application/msword", "xls": "application/vnd.ms-excel", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp", "gif": "image/gif", "bmp": "image/bmp", "tif": "image/tiff", "tiff": "image/tiff"}.get(a.file_type, "application/octet-stream")
    return Response(content=data, media_type=media, headers={"Content-Disposition": f'inline; filename="{a.file_name}"', "X-Content-Type-Options": "nosniff"})


# ---------------------------------------------------------------- pipeline steps
@router.post("/cases/{case_id}/classify")
def classify(case_id: str, user: UserRecord = Depends(require("compare"))):
    s = svc()
    case = s.reprocess(case_id, user, step="classify")
    return _mutation_view(s, case)


@router.post("/cases/{case_id}/extract")
def extract(case_id: str, user: UserRecord = Depends(require("compare"))):
    s = svc()
    case = s.reprocess(case_id, user, step="extract")
    return _mutation_view(s, case)


@router.post("/cases/{case_id}/compare")
def compare(case_id: str, user: UserRecord = Depends(require("compare"))):
    s = svc()
    case = s.reprocess(case_id, user, step="compare")
    return _mutation_view(s, case)


@router.post("/cases/{case_id}/retry")
def retry(case_id: str, user: UserRecord = Depends(require("compare"))):
    s = svc()
    case = s.reprocess(case_id, user, step="retry")
    return _mutation_view(s, case)


@router.post("/cases/{case_id}/upload")
async def upload_document(case_id: str, file: UploadFile = File(...), kind: str = Form(default="auto"), user: UserRecord = Depends(require("compare"))):
    """Upload / re-link a missing SI or Draft BL, then re-run the pipeline."""
    from app.contracts.schemas import AttachmentMeta
    from app.readers.document_reader import read_document

    s = svc()
    case = s.get(case_id)
    e = s.repo.get_email(case.source_email_id)
    try:
        validate_attachment_count(len(e.attachments) + 1)
        name = safe_filename(file.filename or "upload.bin")
    except UnsafeUpload as exc:
        raise HTTPException(400, detail={"error": str(exc), "category": "ATTACHMENT_UPLOAD_ERROR"})
    data = await file.read(max_file_bytes() + 1)
    try:
        validate_file_size(len(data))
    except UnsafeUpload as exc:
        raise HTTPException(413, detail={"error": str(exc), "category": "ATTACHMENT_UPLOAD_ERROR"})
    if kind in ("SI", "BL") and f"_{kind}" not in name.upper():
        stem, _, ext = name.rpartition(".")
        name = f"{stem or 'upload'}_{kind}.{ext or 'txt'}"
    rr = read_document(name, data)
    pointer = f"upload/{e.id}/{name}"
    s.repo.save_blob(pointer, data)
    e.attachments.append(AttachmentMeta(id=f"att_{e.id}_{kind}_{rr.checksum[:8]}", source_email_id=e.id, file_name=name, file_type=rr.file_type, size_bytes=rr.size_bytes,
                                        checksum=rr.checksum, storage_pointer=pointer, extraction_status=rr.status, raw_text=rr.text or None, page_count=rr.page_count,
                                        reader_note=rr.note, ocr=bool(rr.note and "OCR" in rr.note and rr.text)))
    s.repo.save_email(e)
    s.pipe.audit(case.id, "USER", user.id, "DOCUMENT_UPLOADED", after={"file": name, "kind": kind, "status": rr.status.value})
    case = s.pipe.run(e, actor_id=user.id, force=True)
    return _case_view(case, e)


# ---------------------------------------------------------------- human-in-the-loop
@router.post("/cases/{case_id}/draft")
def draft(case_id: str, req: DraftRequest = DraftRequest(), user: UserRecord = Depends(require("generate_draft"))):
    s = svc()
    case = s.generate_draft(case_id, user, draft_type=req.draft_type)
    if req.language and req.language != "en" and case.drafts:
        d = case.drafts[-1]
        d.body = translate_text(d.body, req.language)
        s.repo.save_case(case)
    return _mutation_view(s, case)


@router.post("/cases/{case_id}/draft/edit")
def draft_edit(case_id: str, dec: DraftDecision, user: UserRecord = Depends(require("generate_draft"))):
    s = svc()
    case = s.edit_draft(case_id, dec, user)
    return _mutation_view(s, case)


@router.post("/cases/{case_id}/approve")
def approve(case_id: str, dec: DraftDecision, user: UserRecord = Depends(require("generate_draft"))):
    s = svc()
    case = s.approve_draft(case_id, dec, user)
    return _mutation_view(s, case)


@router.post("/cases/{case_id}/reject")
def reject(case_id: str, dec: DraftDecision, user: UserRecord = Depends(require("generate_draft"))):
    s = svc()
    case = s.reject_draft(case_id, dec, user)
    return _mutation_view(s, case)


@router.post("/cases/{case_id}/assign")
def assign(case_id: str, req: AssignRequest, user: UserRecord = Depends(require("assign"))):
    s = svc()
    case = s.assign(case_id, req, user)
    return _mutation_view(s, case)


@router.post("/cases/{case_id}/no-action")
def no_action(case_id: str, user: UserRecord = Depends(require("mutate_case"))):
    s = svc()
    case = s.mark_no_action(case_id, user)
    return _mutation_view(s, case)


@router.post("/cases/{case_id}/complete")
def complete(case_id: str, body: dict[str, Any] | None = None, user: UserRecord = Depends(require("mutate_case"))):
    s = svc()
    case = s.complete(case_id, user, note=(body or {}).get("note"))
    return _mutation_view(s, case)


@router.post("/cases/{case_id}/request-review")
def request_review(case_id: str, body: dict[str, Any] | None = None, user: UserRecord = Depends(require("mutate_case"))):
    s = svc()
    case = s.request_review(case_id, user, note=(body or {}).get("note"))
    return _mutation_view(s, case)


# ---------------------------------------------------------------- notify party / share
@router.post("/cases/{case_id}/notify-party")
def notify_party_begin(case_id: str, user: UserRecord = Depends(require("share_internal"))):
    return svc().begin_notify_party(case_id, user)


@router.get("/cases/{case_id}/recipients")
def recipients(case_id: str, user: UserRecord = Depends(require("view_case"))):
    return {"recipients": svc().recipient_options(user)}


@router.post("/cases/{case_id}/share")
def share(case_id: str, req: ShareRequest, user: UserRecord = Depends(require("view_case"))):
    return svc().share(case_id, req, user)


@router.post("/cases/{case_id}/share/{share_id}/confirm")
def share_confirm(case_id: str, share_id: str, user: UserRecord = Depends(require("view_case"))):
    return svc().confirm_share(case_id, share_id, user)


@router.post("/shares/{share_id}/acknowledge")
def share_ack(share_id: str, body: dict[str, Any] | None = None, user: UserRecord = Depends(current_user)):
    return svc().acknowledge_share(share_id, user, (body or {}).get("response")).model_dump(mode="json")


# ---------------------------------------------------------------- ask AI / translate
@router.post("/cases/{case_id}/ask", response_model=AskResponse)
def ask(case_id: str, req: AskRequest, user: UserRecord = Depends(require("view_case"))):
    s = svc()
    case = s.get(case_id)
    email = s.repo.get_email(case.source_email_id)
    resp = answer_question(req.question, case, email, s.repo.list_audit(case.id), s.policy(), req.language)
    s.pipe.audit(case.id, "USER", user.id, "ASK_AI", after={"question": req.question[:200], "refused": resp.refused, "generated_by": resp.generated_by})
    return resp


@router.post("/cases/{case_id}/translate")
def translate(case_id: str, body: dict[str, Any], user: UserRecord = Depends(require("view_case"))):
    s = svc()
    case = s.get(case_id)
    email = s.repo.get_email(case.source_email_id)
    target = body.get("target", "en")
    text = body.get("text") or email.body
    return {"original": text, "translated": translate_text(text, target), "target": target, "source_language": email.language}


# ---------------------------------------------------------------- batch / export / submission
@router.post("/cases/batch")
def batch(req: BatchRequest, user: UserRecord = Depends(require("batch"))):
    return svc().batch(req, user)


@router.get("/export/cases.csv", response_class=PlainTextResponse)
def export_csv(user: UserRecord = Depends(require("export_data"))):
    return svc().export_csv()


# ---------------------------------------------------------------- agent-run history
def history_rows(repo, user_id: str, *, result: str = "done", run_by: Optional[str] = None, date_from: Optional[str] = None,
                 date_to: Optional[str] = None, q: Optional[str] = None) -> list[dict[str, Any]]:
    """Cases the AI agent has run, newest run first. `result`: done | paused | error | all."""
    from app.services.reporting import history_rows as _rows

    return _rows(repo, user_id, result=result, run_by=run_by, date_from=date_from, date_to=date_to, q=q)


@router.get("/history")
def agent_history(result: str = "done", run_by: Optional[str] = None, date_from: Optional[str] = None, date_to: Optional[str] = None,
                  q: Optional[str] = None, limit: int = 50, offset: int = 0, user: UserRecord = Depends(require("view_case"))):
    """Processed cases: every case the AI agent has run or a person marked complete (default: finished), each row opens the case."""
    from app.services.reporting import history_result_of

    rows = history_rows(get_repo(), user.id, result=result, run_by=run_by, date_from=date_from, date_to=date_to, q=q)
    cap = max(1, min(limit, 500))
    counts = {"done": 0, "paused": 0, "error": 0}
    for c in get_repo().list_cases():
        outcome = history_result_of(c)
        if outcome:
            counts[outcome] += 1
    return {"total": len(rows), "items": rows[offset: offset + cap], "counts": counts}


@router.get("/export/history.xlsx")
def export_history_xlsx(result: str = "all", run_by: Optional[str] = None, date_from: Optional[str] = None, date_to: Optional[str] = None,
                        q: Optional[str] = None, user: UserRecord = Depends(require("export_data"))):
    from app.services.reporting import build_history_xlsx

    rows = history_rows(get_repo(), user.id, result=result, run_by=run_by, date_from=date_from, date_to=date_to, q=q)
    return Response(content=build_history_xlsx(rows, generated_by=user.id), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": "attachment; filename=novaship-history.xlsx"})


@router.get("/export/history.csv", response_class=PlainTextResponse)
def export_history_csv(result: str = "all", run_by: Optional[str] = None, date_from: Optional[str] = None, date_to: Optional[str] = None,
                       q: Optional[str] = None, user: UserRecord = Depends(require("export_data"))):
    import csv
    import io

    from app.services.reporting import HISTORY_COLUMNS

    rows = history_rows(get_repo(), user.id, result=result, run_by=run_by, date_from=date_from, date_to=date_to, q=q)
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=HISTORY_COLUMNS, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return out.getvalue()


@router.get("/export/cases.xlsx")
def export_xlsx(user: UserRecord = Depends(require("export_data"))):
    return Response(
        content=svc().export_xlsx(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=cases.xlsx"},
    )


@router.get("/export/report.xlsx")
def export_report_xlsx(user: UserRecord = Depends(require("export_data"))):
    """Overall desk report: overview KPIs, seven fields, cases, field results, security, drafts/delivery, operator activity, mailboxes, errors."""
    return Response(
        content=svc().export_report_xlsx(generated_by=user.id),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=novaship-report.xlsx"},
    )


# ---------------------------------------------------------------- self-evaluation (hackathon reference)
def _evaluation_ready() -> None:
    from app.services import evaluation as ev

    if not ev.GROUND_TRUTH.exists() or not ev.SCORING.exists():
        raise HTTPException(404, detail={"error": "the hackathon reference is not available on this server (sdoc-hackathon-docker/data_v2/ground_truth.json + server/scoring.py)",
                                         "category": "DATABASE_ERROR", "recovery": "Run the evaluation on a machine that has the docker bundle: python backend/scripts/evaluate.py", "retryable": False})


@router.get("/evaluate")
def evaluate_desk(server: Optional[str] = None, user: UserRecord = Depends(require("export_data"))):
    """Score the desk's current results against the organiser's reference with the organiser's scoring.py; no pipeline replay."""
    from app.services import evaluation as ev

    _evaluation_ready()
    result = ev.evaluate(get_repo(), server=server or None)
    svc().pipe.audit(None, ActorType.USER, user.id, "SELF_EVALUATION", after={"final_score": result["scoreboard"].get("final_score"), "answered": result["counts"]["answered"],
                                                                                "missing": result["counts"]["missing"], "server": bool(server)})
    return {k: v for k, v in result.items() if k != "submission"}


@router.post("/evaluate")
async def evaluate_desk_with_upload(ground_truth: UploadFile = File(...), server: str = Form(default=""), user: UserRecord = Depends(require("export_data"))):
    """Same scoreboard, but against an uploaded ground_truth.json: lets a deployment without the private file evaluate.
    The file is scored in memory and never stored; the report Markdown is returned inline so no re-upload is needed."""
    from app.services import evaluation as ev

    if not ev.SCORING.exists():
        raise HTTPException(404, detail={"error": "the organiser's scoring.py is not available on this server", "category": "DATABASE_ERROR", "retryable": False})
    raw = await ground_truth.read()
    if len(raw) > 2_000_000:
        raise HTTPException(400, detail={"error": "ground truth file too large (max 2 MB)", "category": "ATTACHMENT_UPLOAD_ERROR"})
    try:
        truth = ev.validate_truth(json.loads(raw.decode("utf-8")))
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(400, detail={"error": f"ground_truth.json is not valid: {exc}", "category": "ATTACHMENT_UPLOAD_ERROR", "retryable": False})
    result = ev.evaluate(get_repo(), server=server.strip() or None, truth=truth)
    svc().pipe.audit(None, ActorType.USER, user.id, "SELF_EVALUATION", after={"final_score": result["scoreboard"].get("final_score"), "answered": result["counts"]["answered"],
                                                                                "missing": result["counts"]["missing"], "server": bool(server.strip()), "reference": "uploaded"})
    out = {k: v for k, v in result.items() if k != "submission"}
    out["reference"] = "uploaded"
    out["report_md"] = ev.render_report_md(result)
    return out


@router.get("/evaluate/report.md", response_class=PlainTextResponse)
def evaluate_report(server: Optional[str] = None, user: UserRecord = Depends(require("export_data"))):
    from app.services import evaluation as ev

    _evaluation_ready()
    return Response(content=ev.render_report_md(ev.evaluate(get_repo(), server=server or None)), media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=novaship-evaluation.md"})


@router.get("/evaluate/submission.json")
def evaluate_submission(user: UserRecord = Depends(require("export_data"))):
    """The submission the evaluation scores: bundle ids only, missing ones filled with the placeholder."""
    from app.services import evaluation as ev

    submission, missing = ev.build_submission(get_repo(), ev.bundle_ids())
    return Response(content=json.dumps(submission, indent=2), media_type="application/json",
                    headers={"Content-Disposition": "attachment; filename=submission.json", "X-Missing": str(len(missing))})


@router.get("/export/submission.json")
def export_submission(user: UserRecord = Depends(require("export_data"))):
    repo = get_repo()
    out = {}
    for c in repo.list_cases():
        e = repo.get_email(c.source_email_id)
        out[e.id] = case_to_submission_row(c, e).model_dump(mode="json")
    return dict(sorted(out.items()))


# ---------------------------------------------------------------- policies
@router.get("/policies")
def get_policy(user: UserRecord = Depends(require("view_policy"))):
    repo = get_repo()
    p = repo.get_active_policy()
    return {"active": p.model_dump(mode="json"), "effective": merged_policy(p.values), "explanation": explain_policy(merged_policy(p.values)), "versions": [v.model_dump(mode="json") for v in repo.list_policy_versions()]}


@router.put("/policies")
def put_policy(body: dict[str, Any], user: UserRecord = Depends(require("edit_policy"))):
    note = body.pop("change_note", "policy update")
    accepted = body.pop("accepted_suggestions", None)
    p = svc().update_policy(body, user, note, accepted_suggestions=accepted if isinstance(accepted, list) else None)
    return {"active": p.model_dump(mode="json"), "explanation": explain_policy(merged_policy(p.values))}


@router.get("/policies/suggestions")
def policy_suggestions(user: UserRecord = Depends(require("view_policy"))):
    """What the desk learned from the security gate and proposes as a policy change (nothing is applied by itself)."""
    from app.ai.policy_learning import suggestions

    repo = get_repo()
    return suggestions(repo, merged_policy(repo.get_active_policy().values))


@router.post("/policies/suggestions/{suggestion_id}/dismiss")
def dismiss_policy_suggestion(suggestion_id: str, body: dict[str, Any] | None = None, user: UserRecord = Depends(require("edit_policy"))):
    from app.ai.policy_learning import suggestions

    repo = get_repo()
    policy = merged_policy(repo.get_active_policy().values)
    if all(s["id"] != suggestion_id for s in suggestions(repo, policy)["items"]):
        raise HTTPException(404, detail={"error": f"suggestion {suggestion_id} is not open", "category": "DATABASE_ERROR"})
    svc().dismiss_suggestion(suggestion_id, user, note=(body or {}).get("note"))
    return suggestions(repo, policy)


@router.get("/contracts/fields")
def contract_fields():
    return {"seven_fields": list(SEVEN_FIELDS), "no_mismatch_message": "No mismatch detected."}
