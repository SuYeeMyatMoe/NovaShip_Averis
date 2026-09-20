"""REST API. Every mutation writes an audit event (via CaseService / Pipeline)."""
from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse, Response

from app.ai.assistant import answer_question, translate_text
from app.auth.rbac import current_user, has_permission, require
from app.config import BUNDLE_DIR, get_repo
from app.contracts.schemas import (
    SEVEN_FIELDS,
    AskRequest,
    AskResponse,
    AssignRequest,
    BatchRequest,
    CaseStatus,
    DraftDecision,
    DraftRequest,
    ShareRequest,
    UserRecord,
)
from app.core.policy import explain_policy, merged_policy
from app.file_security import UnsafeUpload, max_file_bytes, resolve_bundle_attachment, safe_filename, validate_attachment_count, validate_file_size
from app.services.case_service import CaseService
from app.services.submission import case_to_submission_row

router = APIRouter()


def svc() -> CaseService:
    return CaseService(get_repo())


def _case_view(case, email) -> dict[str, Any]:
    d = case.model_dump(mode="json")
    d["email"] = email.model_dump(mode="json") if email else None
    return d


# ---------------------------------------------------------------- health
@router.get("/health")
def health():
    repo = get_repo()
    return {"status": "ok", "backend": type(repo).__name__, "cases": len(repo.list_cases()), "emails": len(repo.list_emails()), "time": datetime.utcnow().isoformat()}


@router.get("/me")
def me(user: UserRecord = Depends(current_user)):
    from app.auth.rbac import PERMISSIONS

    return {**user.model_dump(mode="json"), "permissions": [p for p in PERMISSIONS if has_permission(user, p)]}


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
def connectors_poll(limit: int = 25, user: UserRecord = Depends(require("ingest"))):
    """Poll the configured email connector (Gmail or bundle) and run the pipeline idempotently."""
    from app.connectors.email_connectors import get_connector

    conn = get_connector()
    if conn is None:
        raise HTTPException(400, detail={"error": "EMAIL_PROVIDER is 'none' - set gmail or bundle in .env", "category": "EMAIL_CONNECTOR_ERROR", "recovery": "Configure Gmail OAuth variables and EMAIL_PROVIDER=gmail", "retryable": False})
    s = svc()
    created, skipped = [], 0
    try:
        for msg in conn.fetch(limit=limit):
            incoming_id = msg.raw.get("email_id") or msg.raw.get("id")
            if incoming_id and s.repo.get_email(str(incoming_id)):
                skipped += 1
                continue
            email = s.pipe.ingest_email(msg.raw, msg.blobs, provider=msg.provider, received_at=msg.received_at)
            if email.is_duplicate_of:
                skipped += 1
                continue
            case = s.pipe.run(email, actor_id=user.id)
            created.append(case.id)
    except Exception as exc:  # connector failure is visible + recoverable
        s.pipe.audit(None, "SYSTEM", "connector", "ERROR", after={"category": "EMAIL_CONNECTOR_ERROR", "message": type(exc).__name__})
        raise HTTPException(502, detail={"error": f"connector {conn.name} failed: {type(exc).__name__}", "category": "EMAIL_CONNECTOR_ERROR", "recovery": "Check credentials / network and retry", "retryable": True})
    return {"connector": conn.name, "created": created, "duplicates_skipped": skipped}


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
    cases = repo.list_cases()
    m = {
        "incoming_emails": len(repo.list_emails()),
        "action_required": sum(1 for c in cases if c.action_required and c.status != CaseStatus.COMPLETED),
        "no_action_required": sum(1 for c in cases if not c.action_required),
        "document_verification_cases": sum(1 for c in cases if c.hackathon_category.value == "BL_COMPARISON"),
        "mismatches_detected": sum(1 for c in cases if c.mismatch_count > 0),
        "no_mismatch_cases": sum(1 for c in cases if c.comparison and c.comparison.comparison_status.value == "PASSED"),
        "waiting_for_documents": sum(1 for c in cases if c.status == CaseStatus.WAITING_DOCUMENTS),
        "human_review": sum(1 for c in cases if c.status == CaseStatus.HUMAN_REVIEW),
        "notify_party": sum(1 for c in cases if c.status in (CaseStatus.NOTIFY_PARTY, CaseStatus.AWAITING_RESPONSE)),
        "processing_errors": sum(len([e for e in c.errors if not e.resolved]) for c in cases),
        "security_flagged": sum(1 for c in cases if c.security.outcome.value != "SAFE"),
        "completed": sum(1 for c in cases if c.status == CaseStatus.COMPLETED),
        "avg_processing_ms": round(sum(c.processing_ms for c in cases) / len(cases), 1) if cases else 0,
        "by_status": {},
        "by_intent": {},
        "by_priority": {},
    }
    for c in cases:
        m["by_status"][c.status.value] = m["by_status"].get(c.status.value, 0) + 1
        m["by_intent"][c.intent.value] = m["by_intent"].get(c.intent.value, 0) + 1
        m["by_priority"][c.priority.value] = m["by_priority"].get(c.priority.value, 0) + 1
    return m


# ---------------------------------------------------------------- cases
@router.get("/cases")
def list_cases(
    status: Optional[str] = None, priority: Optional[str] = None, intent: Optional[str] = None, category: Optional[str] = None,
    mismatch: Optional[str] = Query(default=None, description="yes|no"), assigned: Optional[str] = None, shared: Optional[str] = None,
    sender: Optional[str] = None, q: Optional[str] = None, min_confidence: Optional[float] = None, security: Optional[str] = None,
    date_from: Optional[str] = None, date_to: Optional[str] = None, limit: int = 100, offset: int = 0, sort: str = "updated_desc",
    user: UserRecord = Depends(require("view_case")),
):
    repo = get_repo()
    rows = []
    for c in repo.list_cases():
        e = repo.get_email(c.source_email_id)
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
        if assigned and c.assigned_user_id != assigned:
            continue
        if shared and shared not in c.shared_with:
            continue
        if sender and (not e or sender.lower() not in e.sender.lower()):
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
        rows.append({
            "id": c.id, "email_id": c.source_email_id, "subject": e.subject if e else "", "sender": e.sender if e else "", "received_at": e.received_at.isoformat() if e else None,
            "intent": c.intent.value, "category": c.hackathon_category.value, "security": c.security.outcome.value, "action_required": c.action_required,
            "priority": c.priority.value, "si_available": c.si_available, "bl_available": c.bl_available, "attachments": len(e.attachments) if e else 0,
            "mismatch_count": c.mismatch_count, "comparison_status": c.comparison_status.value if c.comparison_status else None, "review_reason": c.review_reason.value if c.review_reason else None,
            "confidence": c.confidence, "assigned_user_id": c.assigned_user_id, "shared_with": c.shared_with, "status": c.status.value, "updated_at": c.updated_at.isoformat(),
            "summary": c.summary.text if c.summary else "", "errors": len([x for x in c.errors if not x.resolved]), "drafts": len(c.drafts),
        })
    key = {"updated_desc": (lambda r: r["updated_at"], True), "received_desc": (lambda r: r["received_at"] or "", True), "priority": (lambda r: {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}[r["priority"]], False), "confidence": (lambda r: r["confidence"], False)}.get(sort, (lambda r: r["updated_at"], True))
    rows.sort(key=key[0], reverse=key[1])
    return {"total": len(rows), "items": rows[offset: offset + limit]}


@router.get("/cases/{case_id}")
def get_case(case_id: str, user: UserRecord = Depends(require("view_case"))):
    s = svc()
    case = s.get(case_id)
    return _case_view(case, s.repo.get_email(case.source_email_id))


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
    signed = getattr(s.repo, "signed_url", lambda *_: None)(a.storage_pointer, 300)
    return {**a.model_dump(mode="json"), "signed_url": signed, "expires_in_s": 300 if signed else None}


@router.get("/cases/{case_id}/documents/{attachment_id}/raw")
def get_document_raw(case_id: str, attachment_id: str, user: UserRecord = Depends(require("view_document"))):
    s = svc()
    case = s.get(case_id)
    e = s.repo.get_email(case.source_email_id)
    a = next((a for a in e.attachments if a.id == attachment_id), None)
    if not a:
        raise HTTPException(404, detail={"error": "attachment not found", "category": "ATTACHMENT_DOWNLOAD_ERROR"})
    data = s.repo.get_blob(a.storage_pointer)
    if data is None:
        try:
            f = resolve_bundle_attachment(BUNDLE_DIR, f"attachments/{safe_filename(a.file_name)}")
        except UnsafeUpload as exc:
            raise HTTPException(400, detail={"error": str(exc), "category": "ATTACHMENT_DOWNLOAD_ERROR"})
        data = f.read_bytes() if f.exists() else b""
    media = {"pdf": "application/pdf", "txt": "text/plain; charset=utf-8", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}.get(a.file_type, "application/octet-stream")
    return Response(content=data, media_type=media, headers={"Content-Disposition": f'inline; filename="{a.file_name}"', "X-Content-Type-Options": "nosniff"})


# ---------------------------------------------------------------- pipeline steps
@router.post("/cases/{case_id}/classify")
def classify(case_id: str, user: UserRecord = Depends(require("compare"))):
    s = svc()
    case = s.reprocess(case_id, user, step="classify")
    return _case_view(case, s.repo.get_email(case.source_email_id))


@router.post("/cases/{case_id}/extract")
def extract(case_id: str, user: UserRecord = Depends(require("compare"))):
    s = svc()
    case = s.reprocess(case_id, user, step="extract")
    return _case_view(case, s.repo.get_email(case.source_email_id))


@router.post("/cases/{case_id}/compare")
def compare(case_id: str, user: UserRecord = Depends(require("compare"))):
    s = svc()
    case = s.reprocess(case_id, user, step="compare")
    return _case_view(case, s.repo.get_email(case.source_email_id))


@router.post("/cases/{case_id}/retry")
def retry(case_id: str, user: UserRecord = Depends(require("compare"))):
    s = svc()
    case = s.reprocess(case_id, user, step="retry")
    return _case_view(case, s.repo.get_email(case.source_email_id))


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
                                        checksum=rr.checksum, storage_pointer=pointer, extraction_status=rr.status, raw_text=rr.text or None, page_count=rr.page_count))
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
    return _case_view(case, s.repo.get_email(case.source_email_id))


@router.post("/cases/{case_id}/draft/edit")
def draft_edit(case_id: str, dec: DraftDecision, user: UserRecord = Depends(require("generate_draft"))):
    s = svc()
    case = s.edit_draft(case_id, dec, user)
    return _case_view(case, s.repo.get_email(case.source_email_id))


@router.post("/cases/{case_id}/approve")
def approve(case_id: str, dec: DraftDecision, user: UserRecord = Depends(require("generate_draft"))):
    s = svc()
    case = s.approve_draft(case_id, dec, user)
    return _case_view(case, s.repo.get_email(case.source_email_id))


@router.post("/cases/{case_id}/reject")
def reject(case_id: str, dec: DraftDecision, user: UserRecord = Depends(require("generate_draft"))):
    s = svc()
    case = s.reject_draft(case_id, dec, user)
    return _case_view(case, s.repo.get_email(case.source_email_id))


@router.post("/cases/{case_id}/assign")
def assign(case_id: str, req: AssignRequest, user: UserRecord = Depends(require("assign"))):
    s = svc()
    case = s.assign(case_id, req, user)
    return _case_view(case, s.repo.get_email(case.source_email_id))


@router.post("/cases/{case_id}/no-action")
def no_action(case_id: str, user: UserRecord = Depends(require("mutate_case"))):
    s = svc()
    case = s.mark_no_action(case_id, user)
    return _case_view(case, s.repo.get_email(case.source_email_id))


@router.post("/cases/{case_id}/complete")
def complete(case_id: str, body: dict[str, Any] | None = None, user: UserRecord = Depends(require("mutate_case"))):
    s = svc()
    case = s.complete(case_id, user, note=(body or {}).get("note"))
    return _case_view(case, s.repo.get_email(case.source_email_id))


@router.post("/cases/{case_id}/request-review")
def request_review(case_id: str, body: dict[str, Any] | None = None, user: UserRecord = Depends(require("mutate_case"))):
    s = svc()
    case = s.request_review(case_id, user, note=(body or {}).get("note"))
    return _case_view(case, s.repo.get_email(case.source_email_id))


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
    p = svc().update_policy(body, user, note)
    return {"active": p.model_dump(mode="json"), "explanation": explain_policy(merged_policy(p.values))}


@router.get("/contracts/fields")
def contract_fields():
    return {"seven_fields": list(SEVEN_FIELDS), "no_mismatch_message": "No mismatch detected."}
