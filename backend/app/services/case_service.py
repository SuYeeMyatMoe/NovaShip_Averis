"""
Case actions: assign / share / notify-party / approve / reject / retry /
complete / batch / policy. Every mutation writes an audit event.
Human-in-the-loop: AI proposes -> human approves/edits/rejects -> action executes.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import uuid
from datetime import datetime
from typing import Any, Optional

from fastapi import HTTPException

from app.ai.assistant import build_share_message
from app.ai.summary_draft import build_draft, polish_with_llm
from app.auth.rbac import has_permission
from app.contracts.schemas import (
    EXTERNAL_RECIPIENT_TYPES,
    ActorType,
    AssignRequest,
    BatchRequest,
    CaseRecord,
    CaseStatus,
    DraftDecision,
    DraftStatus,
    ErrorCategory,
    PolicyRecord,
    ProcessingError,
    RecipientType,
    ShareRecord,
    ShareRequest,
    UserRecord,
)
from app.core.policy import merged_policy
from app.pipeline.orchestrator import Pipeline
from app.repositories.base import BaseRepository


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class DeliveryOutcomeUnknown(RuntimeError):
    """The provider call started, but its acceptance result is not trustworthy."""


class CaseService:
    def __init__(self, repo: BaseRepository) -> None:
        self.repo = repo
        self.pipe = Pipeline(repo)

    # ------------------------------------------------------------ helpers
    def get(self, case_id: str) -> CaseRecord:
        case = self.repo.get_case(case_id)
        if not case:
            raise HTTPException(404, detail={"error": f"case {case_id} not found", "category": "DATABASE_ERROR"})
        return case

    def _status(self, case: CaseRecord, status: CaseStatus, user: UserRecord) -> None:
        before = case.status
        if before != status:
            case.status = status
            self.pipe.audit(case.id, ActorType.USER, user.id, "STATUS_CHANGED", {"status": before.value}, {"status": status.value})

    def policy(self) -> dict[str, Any]:
        return merged_policy(self.repo.get_active_policy().values)

    @staticmethod
    def _confirmed_share_response(share: ShareRecord) -> dict[str, Any]:
        return {
            "share": share.model_dump(mode="json"),
            "requires_confirmation": False,
            "preview": share.message,
            "payload": share.payload_preview,
        }

    def _deliver_email(self, to: list[str], subject: str, body: str, cc: Optional[list[str]] = None) -> tuple[str, Optional[dict[str, Any]]]:
        recipients = [address.strip() for address in to]
        copies = [address.strip() for address in (cc or [])]
        if not recipients or any(not _EMAIL_RE.fullmatch(address) for address in recipients + copies):
            raise ValueError("outbound email contains an invalid recipient")
        mode = os.environ.get("EMAIL_SEND_MODE", "simulate").strip().lower()
        if mode == "simulate":
            return "simulate", None
        from app.connectors.email_connectors import get_outbound_connector

        connector = get_outbound_connector(mode)
        if connector is None:  # pragma: no cover - simulate returned above
            raise RuntimeError("configured outbound connector is unavailable")
        try:
            result = connector.send(recipients, subject, body, copies)
        except Exception as exc:
            raise DeliveryOutcomeUnknown(
                "outbound provider outcome is unknown"
            ) from exc
        return mode, result

    def _delivery_failure(self, case: CaseRecord, user: UserRecord, item_id: str, exc: Exception) -> None:
        err = ProcessingError(id=_id("err_notify"), case_id=case.id, category=ErrorCategory.NOTIFICATION_ERROR, step="outbound_email",
                              message="Outbound email provider rejected or could not accept the message.", safe_details=type(exc).__name__,
                              recovery="Check the configured email provider credentials/network and retry the approved item.", retryable=True)
        case.errors.append(err)
        self.repo.save_error(err)
        self.pipe.audit(case.id, ActorType.SYSTEM, "notifier", "NOTIFICATION_FAILED",
                        after={"item_id": item_id, "provider": os.environ.get("EMAIL_SEND_MODE", "simulate"), "error_type": type(exc).__name__, "retryable": True})

    def _delivery_unknown(self, case: CaseRecord, item_id: str, exc: Exception) -> None:
        cause = exc.__cause__ or exc
        err = ProcessingError(
            id=_id("err_notify_unknown"),
            case_id=case.id,
            category=ErrorCategory.NOTIFICATION_ERROR,
            step="outbound_email",
            message="The provider response was interrupted, so delivery may have occurred.",
            safe_details=type(cause).__name__,
            recovery="Reconcile the provider Sent mailbox using the recipient, subject, and approval time before any manual resend.",
            retryable=False,
        )
        case.errors.append(err)
        self.repo.save_error(err)
        self.pipe.audit(
            case.id,
            ActorType.SYSTEM,
            "notifier",
            "NOTIFICATION_OUTCOME_UNKNOWN",
            after={
                "item_id": item_id,
                "provider": os.environ.get("EMAIL_SEND_MODE", "simulate"),
                "error_type": type(cause).__name__,
                "retryable": False,
            },
        )

    # ------------------------------------------------------------ actions
    def reprocess(self, case_id: str, user: UserRecord, step: str = "all") -> CaseRecord:
        case = self.get(case_id)
        email = self.repo.get_email(case.source_email_id)
        self.pipe.audit(case.id, ActorType.USER, user.id, "RETRY_REQUESTED", after={"step": step})
        return self.pipe.run(email, actor_id=user.id, force=True)

    def assign(self, case_id: str, req: AssignRequest, user: UserRecord) -> CaseRecord:
        case = self.get(case_id)
        if req.user_id and not self.repo.get_user(req.user_id):
            raise HTTPException(400, detail={"error": f"unknown user {req.user_id}", "category": "AUTH_ERROR"})
        before = {"assigned_user_id": case.assigned_user_id, "assigned_team_id": case.assigned_team_id}
        case.assigned_user_id, case.assigned_team_id = req.user_id or case.assigned_user_id, req.team_id or case.assigned_team_id
        self.pipe.audit(case.id, ActorType.USER, user.id, "ASSIGNED", before, {"assigned_user_id": case.assigned_user_id, "assigned_team_id": case.assigned_team_id, "note": req.note})
        if case.status in (CaseStatus.CLASSIFIED, CaseStatus.HUMAN_REVIEW, CaseStatus.DRAFT_READY, CaseStatus.MISMATCH_DETECTED, CaseStatus.NO_MISMATCH_DETECTED):
            self._status(case, CaseStatus.ASSIGNED, user)
        self.repo.save_case(case)
        return case

    def generate_draft(self, case_id: str, user: UserRecord, draft_type: Optional[str] = None, use_llm: bool = True) -> CaseRecord:
        case = self.get(case_id)
        email = self.repo.get_email(case.source_email_id)
        draft = build_draft(email, case.classification, case.comparison, case.review_reason, case.si_available, case.bl_available, draft_type=draft_type)
        if not draft:
            raise HTTPException(400, detail={"error": "No draft is applicable (informational / spam case). Mark as No Reply Needed.", "category": "LOW_CONFIDENCE"})
        if use_llm:
            draft = polish_with_llm(draft, case.comparison)
        draft.id = _id("draft")
        draft.version = len(case.drafts) + 1
        case.drafts.append(draft)
        self.pipe.audit(case.id, ActorType.AI, "draft_generator", "DRAFT_GENERATED", after={"draft_id": draft.id, "type": draft.draft_type, "to": draft.to, "requested_by": user.id})
        if case.status in (CaseStatus.MISMATCH_DETECTED, CaseStatus.NO_MISMATCH_DETECTED, CaseStatus.CLASSIFIED):
            self._status(case, CaseStatus.DRAFT_READY, user)
        self.repo.save_case(case)
        return case

    def _draft(self, case: CaseRecord, draft_id: str):
        d = next((d for d in case.drafts if d.id == draft_id), None)
        if not d:
            raise HTTPException(404, detail={"error": f"draft {draft_id} not found", "category": "DATABASE_ERROR"})
        return d

    def edit_draft(self, case_id: str, dec: DraftDecision, user: UserRecord) -> CaseRecord:
        case = self.get(case_id)
        d = self._draft(case, dec.draft_id)
        before = {"subject": d.subject, "body": d.body}
        if dec.edited_subject is not None:
            d.subject = dec.edited_subject
        if dec.edited_body is not None:
            d.body = dec.edited_body
        d.status, d.version = DraftStatus.EDITED, d.version + 1
        self.pipe.audit(case.id, ActorType.USER, user.id, "DRAFT_EDITED", before, {"subject": d.subject, "body": d.body, "version": d.version, "note": dec.note})
        self.repo.save_case(case)
        return case

    def approve_draft(self, case_id: str, dec: DraftDecision, user: UserRecord) -> CaseRecord:
        """Human approval gate; transport success is required before an item becomes SENT."""
        case = self.get(case_id)
        d = self._draft(case, dec.draft_id)
        if d.status in {DraftStatus.SENT, DraftStatus.SIMULATED}:
            return case
        if d.status in {DraftStatus.DELIVERING, DraftStatus.DELIVERY_UNKNOWN}:
            raise HTTPException(
                409,
                detail={
                    "error": "delivery outcome requires provider reconciliation; automatic resend is blocked",
                    "category": "NOTIFICATION_ERROR",
                    "retryable": False,
                },
            )
        if d.requires_external_approval and not has_permission(user, "approve_send"):
            raise HTTPException(403, detail={"error": "approve_send permission required for external email", "category": "AUTH_ERROR"})
        if dec.edited_body is not None or dec.edited_subject is not None:
            case = self.edit_draft(case_id, dec, user)
            d = self._draft(case, dec.draft_id)
        before = {"status": d.status.value}
        d.status = DraftStatus.APPROVED
        self.pipe.audit(case.id, ActorType.USER, user.id, "DRAFT_APPROVED", before, {"status": d.status.value, "to": d.to, "subject": d.subject, "note": dec.note})
        configured_mode = os.environ.get("EMAIL_SEND_MODE", "simulate").strip().lower()
        if configured_mode != "simulate":
            d.status = DraftStatus.DELIVERING
            self.repo.save_case(case)
        try:
            mode, _provider_result = self._deliver_email(d.to, d.subject, d.body, d.cc)
        except DeliveryOutcomeUnknown as exc:
            d.status = DraftStatus.DELIVERY_UNKNOWN
            self._delivery_unknown(case, d.id or dec.draft_id, exc)
            self.repo.save_case(case)
            raise HTTPException(
                502,
                detail={
                    "error": "outbound delivery outcome is unknown; automatic retry is blocked pending provider reconciliation",
                    "category": "NOTIFICATION_ERROR",
                    "retryable": False,
                },
            )
        except Exception as exc:
            d.status = DraftStatus.SEND_FAILED
            self._delivery_failure(case, user, d.id or dec.draft_id, exc)
            self.repo.save_case(case)
            raise HTTPException(502, detail={"error": "outbound email was not accepted; the approved draft can be retried", "category": "NOTIFICATION_ERROR", "retryable": True})
        accepted = mode == "gmail"
        d.status = DraftStatus.SENT if accepted else DraftStatus.SIMULATED
        action = "NOTIFICATION_SENT" if accepted else "NOTIFICATION_SIMULATED"
        self.pipe.audit(case.id, ActorType.SYSTEM, "notifier", action,
                        after={"channel": "email", "to": d.to, "subject": d.subject, "draft_id": d.id, "mode": mode, "provider_accepted": accepted})
        self._status(case, CaseStatus.AWAITING_RESPONSE, user)
        self.repo.save_case(case)
        return case

    def reject_draft(self, case_id: str, dec: DraftDecision, user: UserRecord) -> CaseRecord:
        case = self.get(case_id)
        d = self._draft(case, dec.draft_id)
        before = {"status": d.status.value}
        d.status = DraftStatus.REJECTED
        self.pipe.audit(case.id, ActorType.USER, user.id, "DRAFT_REJECTED", before, {"status": d.status.value, "note": dec.note})
        self._status(case, CaseStatus.HUMAN_REVIEW, user)
        self.repo.save_case(case)
        return case

    def mark_no_action(self, case_id: str, user: UserRecord) -> CaseRecord:
        case = self.get(case_id)
        case.action_required = False
        self.pipe.audit(case.id, ActorType.USER, user.id, "MARKED_NO_ACTION", after={"action_required": False})
        self._status(case, CaseStatus.NO_ACTION_INFO, user)
        self.repo.save_case(case)
        return case

    def complete(self, case_id: str, user: UserRecord, note: Optional[str] = None) -> CaseRecord:
        case = self.get(case_id)
        self.pipe.audit(case.id, ActorType.USER, user.id, "COMPLETED", after={"note": note})
        self._status(case, CaseStatus.COMPLETED, user)
        self.repo.save_case(case)
        return case

    def request_review(self, case_id: str, user: UserRecord, note: Optional[str] = None) -> CaseRecord:
        case = self.get(case_id)
        self.pipe.audit(case.id, ActorType.USER, user.id, "REVIEW_REQUESTED", after={"note": note})
        self._status(case, CaseStatus.HUMAN_REVIEW, user)
        self.repo.save_case(case)
        return case

    def begin_notify_party(self, case_id: str, user: UserRecord) -> dict[str, Any]:
        """Step 1-3 of the Notify Party flow: show SI/BL values + authorised recipient choices."""
        case = self.get(case_id)
        cmp = case.comparison
        np_field = next((f for f in cmp.fields if f.field == "notify_party"), None) if cmp else None
        self._status(case, CaseStatus.NOTIFY_PARTY, user)
        self.pipe.audit(case.id, ActorType.USER, user.id, "NOTIFY_PARTY_STARTED")
        self.repo.save_case(case)
        return {
            "case_id": case.id,
            "notify_party": None if not np_field else {"si": np_field.si_original, "bl": np_field.bl_original, "result": np_field.result.value, "match": np_field.result.value == "MATCH"},
            "recipients": self.recipient_options(user),
            "mismatch_fields": cmp.mismatch_fields if cmp else [],
            "note": "The extracted Notify Party is a comparison value. Sending requires selecting an authorised recipient and human confirmation.",
        }

    def recipient_options(self, user: UserRecord) -> list[dict[str, Any]]:
        out = []
        for u in self.repo.list_users():
            rtype = RecipientType.SUPERVISOR if "SUPERVISOR" in [r.value for r in u.roles] else RecipientType.OPERATIONS_STAFF
            out.append({"id": u.id, "label": f"{u.display_name} <{u.email}>", "recipient_type": rtype.value, "external": False, "roles": [r.value for r in u.roles], "allowed": has_permission(user, "share_internal")})
        for t in getattr(self.repo, "teams", []):
            out.append({"id": t["id"], "label": t["name"], "recipient_type": RecipientType.TEAM.value, "external": False, "roles": ["TEAM"], "allowed": has_permission(user, "share_internal")})
        for p in self.repo.list_parties():
            out.append({"id": p.id, "label": f"{p.party_name} - {p.name} <{p.email}>", "recipient_type": RecipientType.NOTIFY_PARTY_CONTACT.value, "external": True,
                        "roles": ["APPROVED_PARTY" if p.approved else "UNAPPROVED_PARTY"], "allowed": has_permission(user, "notify_external") and p.approved})
        return out

    def share(self, case_id: str, req: ShareRequest, user: UserRecord) -> dict[str, Any]:
        """Steps 4-9: build preview -> (confirm) -> send/share -> audit -> status."""
        case = self.get(case_id)
        email = self.repo.get_email(case.source_email_id)
        is_external = req.recipient_type in EXTERNAL_RECIPIENT_TYPES
        perm = "notify_external" if is_external else "share_internal"
        if not has_permission(user, perm):
            self.pipe.audit(case.id, ActorType.USER, user.id, "SHARE_DENIED", after={"reason": f"missing permission {perm}", "recipient_type": req.recipient_type.value})
            raise HTTPException(403, detail={"error": f"permission '{perm}' required to share with {req.recipient_type.value}", "category": "AUTH_ERROR"})
        if is_external:
            party = self.repo.get_party(req.recipient_party_id or "")
            if not party:
                raise HTTPException(400, detail={"error": "recipient_party_id required for external recipients", "category": "NOTIFICATION_ERROR"})
            if not party.approved:
                raise HTTPException(403, detail={"error": f"party {party.party_name} is not an approved Notify Party contact", "category": "AUTH_ERROR"})
            label = f"{party.party_name} - {party.name} <{party.email}>"
        elif req.recipient_type == RecipientType.TEAM:
            team = next((t for t in getattr(self.repo, "teams", []) if t["id"] == req.recipient_user_id), None)
            label = team["name"] if team else (req.recipient_user_id or "team")
        else:
            u = self.repo.get_user(req.recipient_user_id or "")
            if not u:
                raise HTTPException(400, detail={"error": "recipient_user_id required for internal recipients", "category": "NOTIFICATION_ERROR"})
            label = f"{u.display_name} <{u.email}>"

        include_fields = (
            req.include_fields
            if "include_fields" in req.model_fields_set
            else None
        )
        message, payload = build_share_message(
            case,
            email,
            label,
            is_external,
            include_fields,
            req.due_date,
        )
        if req.message:
            message = req.message.strip() + "\n\n" + message
        share = ShareRecord(id=_id("share"), case_id=case.id, shared_by=user.id, recipient_type=req.recipient_type, recipient_user_id=req.recipient_user_id,
                            recipient_party_id=req.recipient_party_id, recipient_label=label, is_external=is_external, message=message, payload_preview=payload, due_date=req.due_date)
        policy = self.policy()
        needs_confirm = is_external and policy["communication"]["external_drafts_require_confirmation"]
        if req.preview_only or (needs_confirm and not req.confirm_external):
            share.status = "PENDING_CONFIRMATION"
            self.repo.save_share(share)
            self.pipe.audit(case.id, ActorType.USER, user.id, "SHARE_CREATED", after={"share_id": share.id, "recipient_label": label, "external": is_external, "status": share.status})
            return {"share": share.model_dump(mode="json"), "requires_confirmation": True, "preview": message, "payload": payload}
        configured_mode = os.environ.get("EMAIL_SEND_MODE", "simulate").strip().lower()
        live_external = is_external and configured_mode != "simulate"
        share.status = "DELIVERING" if live_external else "CONFIRMING"
        share.confirmation_started_at = datetime.utcnow()
        share.delivery_provider = configured_mode if live_external else None
        self.repo.save_share(share)
        return self._finalize_share(share, user, owns_claim=True)

    def _finalize_share(
        self,
        share: ShareRecord,
        user: UserRecord,
        *,
        owns_claim: bool,
    ) -> dict[str, Any]:
        if share.status in {"SENT", "SIMULATED"}:
            return self._confirmed_share_response(share)

        case = self.get(share.case_id)
        delivery_mode = "internal"
        final_status = "SENT"
        if share.is_external:
            configured_mode = (
                share.delivery_provider
                if share.status in {"DELIVERING", "DELIVERY_ACCEPTED"}
                and share.delivery_provider
                else os.environ.get("EMAIL_SEND_MODE", "simulate")
            ).strip().lower()
            if share.status == "DELIVERING" and not owns_claim:
                raise HTTPException(
                    409,
                    detail={
                        "error": "delivery outcome is still being reconciled; automatic resend is blocked",
                        "category": "NOTIFICATION_ERROR",
                        "retryable": False,
                    },
                )
            if share.status == "DELIVERY_ACCEPTED":
                delivery_mode = share.delivery_provider or configured_mode
            else:
                party = self.repo.get_party(share.recipient_party_id or "")
                if not party or not party.approved:
                    raise HTTPException(
                        403,
                        detail={
                            "error": "approved external recipient is no longer available",
                            "category": "AUTH_ERROR",
                        },
                    )
                if configured_mode == "simulate":
                    delivery_mode = "simulate"
                    final_status = "SIMULATED"
                else:
                    if not owns_claim:
                        raise HTTPException(
                            409,
                            detail={
                                "error": "share confirmation is already in progress",
                                "category": "NOTIFICATION_ERROR",
                                "retryable": True,
                            },
                        )
                    if share.status != "DELIVERING":
                        share.status = "DELIVERING"
                        share.delivery_provider = configured_mode
                        self.repo.save_share(share)
                    try:
                        delivery_mode, provider_result = self._deliver_email(
                            [party.email],
                            f"NovaShip case {case.id}",
                            share.message,
                        )
                    except DeliveryOutcomeUnknown as exc:
                        share.status = "DELIVERY_UNKNOWN"
                        self.repo.save_share(share)
                        self._delivery_unknown(case, share.id, exc)
                        self.repo.save_case(case)
                        raise HTTPException(
                            502,
                            detail={
                                "error": "outbound delivery outcome is unknown; automatic retry is blocked pending provider reconciliation",
                                "category": "NOTIFICATION_ERROR",
                                "retryable": False,
                            },
                        )
                    except Exception as exc:
                        share.status = "DELIVERY_FAILED"
                        self.repo.save_share(share)
                        self._delivery_failure(case, user, share.id, exc)
                        self.repo.save_case(case)
                        raise HTTPException(502, detail={"error": "outbound share was not accepted; confirmation can be retried", "category": "NOTIFICATION_ERROR", "retryable": True})
                    share.status = "DELIVERY_ACCEPTED"
                    share.delivery_provider = delivery_mode
                    share.provider_message_id = (provider_result or {}).get("id")
                    share.delivery_accepted_at = datetime.utcnow()
                    self.repo.save_share(share)

        confirmed_share = self.repo.complete_share_confirmation(
            share.id,
            user.id,
            final_status,
            delivery_mode,
        )
        if confirmed_share is None:
            raise HTTPException(
                409,
                detail={
                    "error": "share confirmation was not applied",
                    "category": "NOTIFICATION_ERROR",
                },
            )
        return self._confirmed_share_response(confirmed_share)

    def confirm_share(self, case_id: str, share_id: str, user: UserRecord) -> dict[str, Any]:
        share = self.repo.get_share(share_id)
        if not share or share.case_id != case_id:
            raise HTTPException(404, detail={"error": "share not found", "category": "DATABASE_ERROR"})
        case = self.get(case_id)
        permission = "notify_external" if share.is_external else "share_internal"
        if not has_permission(user, permission):
            self.pipe.audit(
                case.id,
                ActorType.USER,
                user.id,
                "SHARE_DENIED",
                after={
                    "reason": f"missing permission {permission}",
                    "share_id": share.id,
                    "recipient_type": share.recipient_type.value,
                },
            )
            raise HTTPException(
                403,
                detail={
                    "error": f"permission '{permission}' required to confirm this share",
                    "category": "AUTH_ERROR",
                },
            )

        if share.status in {"SENT", "SIMULATED"}:
            return self._confirmed_share_response(share)
        if share.status in {"DELIVERING", "DELIVERY_UNKNOWN"}:
            raise HTTPException(
                409,
                detail={
                    "error": "delivery outcome is still being reconciled; automatic resend is blocked",
                    "category": "NOTIFICATION_ERROR",
                    "retryable": False,
                },
            )

        if share.is_external and share.status != "DELIVERY_ACCEPTED":
            party = self.repo.get_party(share.recipient_party_id or "")
            if not party or not party.approved:
                raise HTTPException(
                    403,
                    detail={
                        "error": "external recipient is no longer approved",
                        "category": "AUTH_ERROR",
                    },
                )

        owns_claim = False
        if share.status in {"PENDING_CONFIRMATION", "DELIVERY_FAILED"}:
            configured_mode = os.environ.get("EMAIL_SEND_MODE", "simulate").strip().lower()
            live_external = share.is_external and configured_mode != "simulate"
            target_status = "DELIVERING" if live_external else "CONFIRMING"
            claimed_share = self.repo.claim_share_confirmation(
                share.id,
                share.status,
                target_status,
                datetime.utcnow(),
                configured_mode if live_external else None,
            )
            owns_claim = claimed_share is not None
            share = claimed_share or self.repo.get_share(share.id)
            if share is None:
                raise HTTPException(
                    409,
                    detail={
                        "error": "share confirmation was not applied",
                        "category": "NOTIFICATION_ERROR",
                    },
                )
        if share.status in {"SENT", "SIMULATED"}:
            return self._confirmed_share_response(share)
        if share.status in {"DELIVERING", "DELIVERY_UNKNOWN"} and not owns_claim:
            raise HTTPException(
                409,
                detail={
                    "error": "delivery outcome is still being reconciled; automatic resend is blocked",
                    "category": "NOTIFICATION_ERROR",
                    "retryable": False,
                },
            )
        if share.status not in {"CONFIRMING", "DELIVERING", "DELIVERY_ACCEPTED"}:
            raise HTTPException(
                409,
                detail={
                    "error": f"share in status {share.status} cannot be confirmed",
                    "category": "NOTIFICATION_ERROR",
                },
            )

        return self._finalize_share(share, user, owns_claim=owns_claim)

    def acknowledge_share(self, share_id: str, user: UserRecord, response: Optional[str]) -> ShareRecord:
        share = self.repo.get_share(share_id)
        if not share:
            raise HTTPException(404, detail={"error": "share not found", "category": "DATABASE_ERROR"})
        if share.is_external or not share.recipient_user_id or share.recipient_user_id != user.id:
            raise HTTPException(403, detail={"error": "only the intended internal recipient may acknowledge this share", "category": "AUTH_ERROR"})
        share.viewed_at = share.viewed_at or datetime.utcnow()
        share.acknowledged_at, share.response, share.status = datetime.utcnow(), response, "ACKNOWLEDGED"
        self.repo.save_share(share)
        self.pipe.audit(share.case_id, ActorType.USER, user.id, "SHARE_ACKNOWLEDGED", after={"share_id": share.id, "response": response})
        return share

    # ------------------------------------------------------------ batch
    def batch(self, req: BatchRequest, user: UserRecord) -> dict[str, Any]:
        results: dict[str, Any] = {}
        if req.action in ("draft", "request_review") and not req.confirm:
            return {"requires_confirmation": True, "action": req.action, "count": len(req.case_ids), "note": "Batch drafts are generated but never sent; confirm to proceed."}
        for cid in req.case_ids:
            try:
                if req.action == "classify" or req.action == "compare":
                    c = self.reprocess(cid, user)
                elif req.action == "mark_no_action":
                    c = self.mark_no_action(cid, user)
                elif req.action == "assign":
                    c = self.assign(cid, AssignRequest(**req.params), user)
                elif req.action == "draft":
                    c = self.generate_draft(cid, user, use_llm=False)
                elif req.action == "archive":
                    c = self.complete(cid, user, note="batch archive")
                elif req.action == "request_review":
                    c = self.request_review(cid, user, note=req.params.get("note"))
                elif req.action == "export":
                    c = self.get(cid)
                else:
                    raise HTTPException(400, detail={"error": f"unknown batch action {req.action}"})
                results[cid] = {"ok": True, "status": c.status.value}
            except HTTPException as exc:
                results[cid] = {"ok": False, "error": exc.detail}
        self.pipe.audit(None, ActorType.USER, user.id, "BATCH_ACTION", after={"action": req.action, "count": len(req.case_ids), "ok": sum(1 for r in results.values() if r["ok"])})
        out: dict[str, Any] = {"requires_confirmation": False, "results": results}
        if req.action == "export":
            out["csv"] = self.export_csv(req.case_ids)
        return out

    def export_csv(self, case_ids: Optional[list[str]] = None) -> str:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["case_id", "email_id", "sender", "subject", "intent", "category", "security", "priority", "status", "comparison_status", "mismatch_count", "mismatch_fields", "review_reason", "confidence", "assigned_user_id", "updated_at"])
        for c in self.repo.list_cases():
            if case_ids and c.id not in case_ids:
                continue
            e = self.repo.get_email(c.source_email_id)
            w.writerow([c.id, c.source_email_id, e.sender if e else "", e.subject if e else "", c.intent.value, c.hackathon_category.value, c.security.outcome.value, c.priority.value,
                        c.status.value, c.comparison_status.value if c.comparison_status else "", c.mismatch_count, "|".join(c.comparison.mismatch_fields) if c.comparison else "",
                        c.review_reason.value if c.review_reason else "", c.confidence, c.assigned_user_id or "", c.updated_at.isoformat()])
        return buf.getvalue()

    # ------------------------------------------------------------ policy
    def update_policy(self, values: dict[str, Any], user: UserRecord, note: str) -> PolicyRecord:
        current = self.repo.get_active_policy()
        n = len(self.repo.list_policy_versions()) + 1
        new = PolicyRecord(id=f"pol_v{n}", version=f"v{n}", name=current.name, values=merged_policy({**current.values, **values}), updated_by=user.id, updated_at=datetime.utcnow(), change_note=note)
        self.repo.save_policy_version(new)
        self.pipe.audit(None, ActorType.USER, user.id, "POLICY_UPDATED", {"version": current.version}, {"version": new.version, "note": note, "changed_sections": list(values.keys())}, policy_version=new.version)
        return new
