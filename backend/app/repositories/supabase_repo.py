"""
Supabase / PostgreSQL repository (PostgREST via supabase-py).

Env:
  SUPABASE_URL              https://<project-ref>.supabase.co
  SUPABASE_SECRET_KEY or legacy SUPABASE_SERVICE_ROLE_KEY (backend-only)
  SUPABASE_STORAGE_BUCKET   default "documents"

Tables: see supabase/migrations/0001_schema.sql. Rich objects (CaseRecord)
are stored as JSONB `payload` PLUS normalized columns/tables so SQL reporting,
RLS and the dashboard queries stay simple. The normalized child tables
(comparison_fields, extracted_fields, drafts, ...) are rewritten on every
case save (idempotent upsert on deterministic ids).
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Optional

from app.contracts.schemas import (
    SEVEN_FIELDS,
    AuditEvent,
    CaseRecord,
    EmailMessage,
    PartyContact,
    PolicyRecord,
    ProcessingError,
    ShareRecord,
    UserRecord,
)
from app.core.policy import DEFAULT_POLICY
from app.repositories.base import BaseRepository, StorageAuthorizationError, StorageProviderError


def _j(model) -> dict[str, Any]:
    return json.loads(model.model_dump_json())


def _storage_failure(operation: str, exc: Exception, *, allow_not_found: bool = False) -> None:
    details = exc.args[0] if exc.args and isinstance(exc.args[0], dict) else {}
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None) or details.get("statusCode") or details.get("status")
    text = f"{details.get('error', '')} {details.get('message', '')} {exc}".lower()
    if allow_not_found and (str(status) == "404" or "not found" in text or "not_found" in text):
        return None
    if str(status) in {"401", "403"} or any(word in text for word in ("unauthorized", "forbidden", "permission")):
        raise StorageAuthorizationError(f"Supabase Storage {operation} was not authorized") from exc
    raise StorageProviderError(f"Supabase Storage {operation} failed ({type(exc).__name__})") from exc


class SupabaseRepository(BaseRepository):
    def __init__(self) -> None:
        from supabase import create_client

        from app.config import supabase_server_credentials

        url, key = supabase_server_credentials()
        self.client = create_client(url, key)
        self.bucket = os.environ.get("SUPABASE_STORAGE_BUCKET", "documents")
        self.tenant = os.environ.get("TENANT_ID", "tenant_april")

    # ---- helpers ----------------------------------------------------------
    def _t(self, name: str):
        return self.client.table(name)

    # ---- emails -----------------------------------------------------------
    def save_email(self, email: EmailMessage) -> None:
        row = {
            "id": email.id, "tenant_id": self.tenant, "provider": email.provider, "provider_message_id": email.provider_message_id,
            "conversation_id": email.conversation_id, "sender": email.sender, "sender_name": email.sender_name,
            "recipients": email.recipients, "cc": email.cc, "subject": email.subject, "body": email.body,
            "received_at": email.received_at.isoformat(), "language": email.language, "checksum": email.checksum,
            "is_duplicate_of": email.is_duplicate_of, "payload": _j(email),
        }
        self._t("email_messages").upsert(row).execute()
        for a in email.attachments:
            self._t("attachments").upsert({
                "id": a.id, "tenant_id": self.tenant, "source_email_id": email.id, "file_name": a.file_name, "file_type": a.file_type,
                "size_bytes": a.size_bytes, "checksum": a.checksum, "storage_pointer": a.storage_pointer,
                "detected_type": a.detected_type.value, "detection_confidence": a.detection_confidence,
                "extraction_status": a.extraction_status.value, "extraction_confidence": a.extraction_confidence,
                "raw_text": a.raw_text, "page_count": a.page_count, "is_duplicate_of": a.is_duplicate_of,
            }).execute()

    def get_email(self, email_id: str) -> Optional[EmailMessage]:
        res = (
            self._t("email_messages")
            .select("payload")
            .eq("id", email_id)
            .eq("tenant_id", self.tenant)
            .limit(1)
            .execute()
        )
        return EmailMessage(**res.data[0]["payload"]) if res.data else None

    def list_emails(self) -> list[EmailMessage]:
        res = self._t("email_messages").select("payload").eq("tenant_id", self.tenant).execute()
        return [EmailMessage(**r["payload"]) for r in res.data]

    def find_email_by_checksum(self, checksum: str) -> Optional[EmailMessage]:
        res = (
            self._t("email_messages")
            .select("payload")
            .eq("checksum", checksum)
            .eq("tenant_id", self.tenant)
            .limit(1)
            .execute()
        )
        return EmailMessage(**res.data[0]["payload"]) if res.data else None

    def find_attachment_by_checksum(self, checksum: str) -> Optional[str]:
        res = (
            self._t("attachments")
            .select("id")
            .eq("checksum", checksum)
            .eq("tenant_id", self.tenant)
            .gt("size_bytes", 0)
            .limit(1)
            .execute()
        )
        return res.data[0]["id"] if res.data else None

    def save_blob(self, pointer: str, data: bytes) -> None:
        try:
            self.client.storage.from_(self.bucket).upload(pointer, data, {"upsert": "true"})
        except Exception as exc:
            _storage_failure("upload", exc)

    def get_blob(self, pointer: str) -> Optional[bytes]:
        try:
            return self.client.storage.from_(self.bucket).download(pointer)
        except Exception as exc:
            return _storage_failure("download", exc, allow_not_found=True)

    def signed_url(self, pointer: str, expires_s: int = 300) -> Optional[str]:
        try:
            return self.client.storage.from_(self.bucket).create_signed_url(pointer, expires_s)["signedURL"]
        except Exception as exc:
            return _storage_failure("signed URL creation", exc, allow_not_found=True)

    # ---- cases ------------------------------------------------------------
    def save_case(self, case: CaseRecord) -> None:
        case.updated_at = datetime.utcnow()
        row = {
            "id": case.id, "tenant_id": self.tenant, "source_email_id": case.source_email_id, "intent": case.intent.value,
            "hackathon_category": case.hackathon_category.value, "action_required": case.action_required, "priority": case.priority.value,
            "status": case.status.value, "security_outcome": case.security.outcome.value, "mismatch_count": case.mismatch_count,
            "comparison_status": case.comparison_status.value if case.comparison_status else None,
            "review_reason": case.review_reason.value if case.review_reason else None, "confidence": case.confidence,
            "si_available": case.si_available, "bl_available": case.bl_available, "si_document_id": case.si_document_id,
            "bl_document_id": case.bl_document_id, "assigned_user_id": case.assigned_user_id, "assigned_team_id": case.assigned_team_id,
            "shared_with": case.shared_with, "processing_ms": case.processing_ms, "created_at": case.created_at.isoformat(),
            "updated_at": case.updated_at.isoformat(), "payload": _j(case),
        }
        self._t("cases").upsert(row).execute()
        # Replace case-owned projections so they exactly mirror the canonical payload.
        # Audit history and shares are intentionally append/persist-only and untouched.
        for table in ("comparison_fields", "extracted_fields", "case_summaries", "action_recommendations", "drafts", "assignments", "comparisons"):
            self._t(table).delete().eq("case_id", case.id).execute()
        # normalized children
        if case.comparison:
            cmp = case.comparison
            self._t("comparisons").upsert({
                "id": f"cmp_{case.id}", "case_id": case.id, "tenant_id": self.tenant, "comparison_status": cmp.comparison_status.value,
                "mismatch_count": cmp.mismatch_count, "required_field_count": cmp.required_field_count, "message": cmp.message,
                "mismatch_fields": cmp.mismatch_fields, "review_fields": cmp.review_fields, "review_reason": cmp.review_reason.value if cmp.review_reason else None,
                "compared_at": cmp.compared_at.isoformat(), "si_document_id": cmp.si_document_id, "bl_document_id": cmp.bl_document_id, "policy_version": cmp.policy_version,
            }).execute()
            self._t("comparison_fields").upsert([{
                "id": f"cf_{case.id}_{f.field}", "comparison_id": f"cmp_{case.id}", "case_id": case.id, "tenant_id": self.tenant, "field_name": f.field,
                "si_original": f.si_original, "bl_original": f.bl_original, "si_normalized": None if f.si_normalized is None else str(f.si_normalized),
                "bl_normalized": None if f.bl_normalized is None else str(f.bl_normalized), "result": f.result.value, "confidence": f.confidence,
                "reason": f.reason, "attention": f.attention, "si_evidence": _j(f.si_evidence), "bl_evidence": _j(f.bl_evidence),
            } for f in cmp.fields]).execute()
        for kind, ext in (("SI", case.si_extraction), ("BL", case.bl_extraction)):
            if ext:
                self._t("extracted_fields").upsert([{
                    "id": f"xf_{case.id}_{kind}_{f}", "case_id": case.id, "tenant_id": self.tenant, "document_kind": kind, "field_name": f,
                    "original_value": ext.get(f).original, "normalized_value": None if ext.get(f).normalized is None else str(ext.get(f).normalized),
                    "confidence": ext.get(f).confidence, "needs_review": ext.get(f).needs_review, "evidence": _j(ext.get(f).evidence),
                } for f in SEVEN_FIELDS]).execute()
        if case.summary:
            self._t("case_summaries").upsert({"id": f"sum_{case.id}", "case_id": case.id, "tenant_id": self.tenant, "text": case.summary.text,
                                               "generated_by": case.summary.generated_by, "evidence_refs": case.summary.evidence_refs, "confidence": case.summary.confidence}).execute()
        if case.recommendation:
            r = case.recommendation
            self._t("action_recommendations").upsert({"id": f"rec_{case.id}", "case_id": case.id, "tenant_id": self.tenant, "action_required": r.action_required,
                                                       "action_type": r.action_type.value, "priority": r.priority.value, "reason": r.reason, "recommended_action": r.recommended_action,
                                                       "responsible_role": r.responsible_role.value, "confidence": r.confidence}).execute()
        for d in case.drafts:
            self._t("drafts").upsert({"id": d.id, "case_id": case.id, "tenant_id": self.tenant, "draft_type": d.draft_type, "to_recipients": d.to, "cc_recipients": d.cc,
                                       "subject": d.subject, "body": d.body, "evidence_refs": d.evidence_refs, "status": d.status.value, "version": d.version,
                                       "requires_external_approval": d.requires_external_approval, "generated_by": d.generated_by}).execute()
        if case.assigned_user_id or case.assigned_team_id:
            self._t("assignments").upsert({"id": f"asg_{case.id}", "case_id": case.id, "tenant_id": self.tenant, "assigned_user_id": case.assigned_user_id,
                                            "assigned_team_id": case.assigned_team_id, "assigned_at": case.updated_at.isoformat()}).execute()

    def get_case(self, case_id: str) -> Optional[CaseRecord]:
        res = (
            self._t("cases")
            .select("payload")
            .eq("id", case_id)
            .eq("tenant_id", self.tenant)
            .limit(1)
            .execute()
        )
        return CaseRecord(**res.data[0]["payload"]) if res.data else None

    def get_case_by_email(self, email_id: str) -> Optional[CaseRecord]:
        res = (
            self._t("cases")
            .select("payload")
            .eq("source_email_id", email_id)
            .eq("tenant_id", self.tenant)
            .limit(1)
            .execute()
        )
        return CaseRecord(**res.data[0]["payload"]) if res.data else None

    def list_cases(self) -> list[CaseRecord]:
        res = self._t("cases").select("payload").eq("tenant_id", self.tenant).order("updated_at", desc=True).limit(2000).execute()
        return [CaseRecord(**r["payload"]) for r in res.data]

    # ---- audit / errors / shares -----------------------------------------
    def append_audit(self, event: AuditEvent) -> None:
        self._t("audit_events").insert({**_j(event), "tenant_id": self.tenant}).execute()

    def append_audit_once(self, event: AuditEvent) -> bool:
        res = (
            self._t("audit_events")
            .upsert(
                {**_j(event), "tenant_id": self.tenant},
                on_conflict="event_id",
                ignore_duplicates=True,
            )
            .execute()
        )
        return bool(res.data)

    def list_audit(self, case_id: Optional[str] = None) -> list[AuditEvent]:
        q = self._t("audit_events").select("*").eq("tenant_id", self.tenant).order("timestamp")
        if case_id:
            q = q.eq("case_id", case_id)
        return [AuditEvent(**{k: v for k, v in r.items() if k != "tenant_id"}) for r in q.limit(5000).execute().data]

    def save_error(self, err: ProcessingError) -> None:
        self._t("processing_errors").upsert({**_j(err), "tenant_id": self.tenant}).execute()

    def save_share(self, share: ShareRecord) -> None:
        self._t("shares").upsert({**_j(share), "tenant_id": self.tenant}).execute()

    def claim_share_confirmation(
        self,
        share_id: str,
        expected_status: str,
        target_status: str,
        started_at: datetime,
        delivery_provider: Optional[str] = None,
    ) -> Optional[ShareRecord]:
        """Claim a retryable confirmation before any provider call."""
        if (
            expected_status not in {"PENDING_CONFIRMATION", "DELIVERY_FAILED"}
            or target_status not in {"CONFIRMING", "DELIVERING"}
            or (target_status == "DELIVERING") != bool(delivery_provider)
        ):
            return None
        res = (
            self._t("shares")
            .update(
                {
                    "status": target_status,
                    "confirmation_started_at": started_at.isoformat(),
                    "delivery_provider": delivery_provider,
                }
            )
            .eq("id", share_id)
            .eq("tenant_id", self.tenant)
            .eq("status", expected_status)
            .execute()
        )
        if not res.data:
            return None
        return ShareRecord(
            **{key: value for key, value in res.data[0].items() if key != "tenant_id"}
        )

    def complete_share_confirmation(
        self,
        share_id: str,
        actor_id: str,
        final_status: str,
        delivery_mode: str,
    ) -> Optional[ShareRecord]:
        """Finalize case, audit, and share state in one PostgreSQL transaction."""
        res = self.client.rpc(
            "complete_share_confirmation",
            {
                "p_share_id": share_id,
                "p_tenant_id": self.tenant,
                "p_actor_id": actor_id,
                "p_final_status": final_status,
                "p_delivery_mode": delivery_mode,
            },
        ).execute()
        if not res.data:
            return None
        return ShareRecord(
            **{key: value for key, value in res.data[0].items() if key != "tenant_id"}
        )

    def list_shares(self, case_id: Optional[str] = None) -> list[ShareRecord]:
        q = self._t("shares").select("*").eq("tenant_id", self.tenant)
        if case_id:
            q = q.eq("case_id", case_id)
        return [ShareRecord(**{k: v for k, v in r.items() if k != "tenant_id"}) for r in q.execute().data]

    def get_share(self, share_id: str) -> Optional[ShareRecord]:
        res = (
            self._t("shares")
            .select("*")
            .eq("id", share_id)
            .eq("tenant_id", self.tenant)
            .limit(1)
            .execute()
        )
        return ShareRecord(**{k: v for k, v in res.data[0].items() if k != "tenant_id"}) if res.data else None

    # ---- users / parties / policy ----------------------------------------
    def list_users(self) -> list[UserRecord]:
        res = self._t("users").select("*, user_roles(role_id)").eq("tenant_id", self.tenant).execute()
        out = []
        for r in res.data:
            roles = [x["role_id"] for x in (r.get("user_roles") or [])]
            out.append(UserRecord(id=r["id"], email=r["email"], display_name=r["display_name"], roles=roles, team_id=r.get("team_id"), tenant_id=r["tenant_id"], is_external=r.get("is_external", False), auth_user_id=r.get("auth_user_id")))
        return out

    def get_user(self, user_id: str) -> Optional[UserRecord]:
        return next((u for u in self.list_users() if u.id == user_id), None)

    def get_user_by_auth_subject(self, subject: str) -> Optional[UserRecord]:
        res = self._t("users").select("*, user_roles(role_id)").eq("tenant_id", self.tenant).eq("auth_user_id", subject).limit(1).execute()
        if not res.data:
            # Backwards compatibility for installations that historically used users.id as sub.
            return self.get_user(subject)
        r = res.data[0]
        return UserRecord(id=r["id"], email=r["email"], display_name=r["display_name"], roles=[x["role_id"] for x in (r.get("user_roles") or [])],
                          team_id=r.get("team_id"), tenant_id=r["tenant_id"], is_external=r.get("is_external", False), auth_user_id=r.get("auth_user_id"))

    def save_user(self, user: UserRecord) -> None:
        self._t("users").upsert({"id": user.id, "tenant_id": self.tenant, "email": user.email, "display_name": user.display_name,
                                 "team_id": user.team_id, "is_external": user.is_external, "auth_user_id": user.auth_user_id}).execute()
        self._t("user_roles").delete().eq("user_id", user.id).execute()
        if user.roles:
            self._t("user_roles").insert([{"user_id": user.id, "role_id": r.value} for r in user.roles]).execute()

    # local password login (migration 0004). Supabase Auth JWTs are accepted independently.
    def get_password_hash(self, user_id: str) -> Optional[str]:
        res = self._t("user_credentials").select("password_hash").eq("user_id", user_id).limit(1).execute()
        return res.data[0]["password_hash"] if res.data else None

    def set_password_hash(self, user_id: str, password_hash: str) -> None:
        self._t("user_credentials").upsert({"user_id": user_id, "password_hash": password_hash, "updated_at": datetime.utcnow().isoformat()}).execute()

    def revoke_session(self, session_id: str) -> None:
        self._t("revoked_sessions").upsert({"session_id": session_id, "revoked_at": datetime.utcnow().isoformat()}).execute()

    def is_session_revoked(self, session_id: str) -> bool:
        res = self._t("revoked_sessions").select("session_id").eq("session_id", session_id).limit(1).execute()
        return bool(res.data)

    def list_parties(self) -> list[PartyContact]:
        res = self._t("party_contacts").select("*").eq("tenant_id", self.tenant).execute()
        return [PartyContact(**r) for r in res.data]

    def get_party(self, party_id: str) -> Optional[PartyContact]:
        res = (
            self._t("party_contacts")
            .select("*")
            .eq("id", party_id)
            .eq("tenant_id", self.tenant)
            .limit(1)
            .execute()
        )
        return PartyContact(**res.data[0]) if res.data else None

    def get_active_policy(self) -> PolicyRecord:
        res = self._t("policy_versions").select("*").eq("tenant_id", self.tenant).order("created_at", desc=True).limit(1).execute()
        if not res.data:
            return PolicyRecord(id="pol_default", version="v1", name="default", values=DEFAULT_POLICY, updated_by="system", updated_at=datetime.utcnow())
        r = res.data[0]
        return PolicyRecord(id=r["id"], version=r["version"], name=r["name"], values=r["values"], updated_by=r["updated_by"], updated_at=r["created_at"], change_note=r.get("change_note", ""))

    def save_policy_version(self, policy: PolicyRecord) -> None:
        self._t("policies").upsert({"id": "policy_active", "tenant_id": self.tenant, "name": policy.name, "active_version": policy.version}).execute()
        self._t("policy_versions").insert({"id": policy.id, "tenant_id": self.tenant, "policy_id": "policy_active", "version": policy.version, "name": policy.name,
                                           "values": policy.values, "updated_by": policy.updated_by, "change_note": policy.change_note, "created_at": policy.updated_at.isoformat()}).execute()

    def list_policy_versions(self) -> list[PolicyRecord]:
        res = self._t("policy_versions").select("*").eq("tenant_id", self.tenant).order("created_at").execute()
        return [PolicyRecord(id=r["id"], version=r["version"], name=r["name"], values=r["values"], updated_by=r["updated_by"], updated_at=r["created_at"], change_note=r.get("change_note", "")) for r in res.data]

    # ---- idempotency ------------------------------------------------------
    def seen_job(self, job_key: str) -> bool:
        res = self._t("ai_runs").select("id").eq("id", job_key).limit(1).execute()
        return bool(res.data)

    def mark_job(self, job_key: str, result: dict[str, Any]) -> None:
        self._t("ai_runs").upsert({"id": job_key, "tenant_id": self.tenant, "result": result, "created_at": datetime.utcnow().isoformat()}).execute()
