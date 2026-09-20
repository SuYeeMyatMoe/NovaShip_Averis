"""In-memory repository (fixtures / tests / offline demo). Thread-safe enough for a demo."""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from app.contracts.schemas import (
    ActorType,
    AuditEvent,
    CaseRecord,
    CaseStatus,
    EmailMessage,
    PartyContact,
    PolicyRecord,
    ProcessingError,
    Role,
    ShareRecord,
    UserRecord,
)
from app.core.policy import DEFAULT_POLICY
from app.repositories.base import BaseRepository

DEFAULT_USERS = [
    UserRecord(id="u_ops_1", email="hanna_azhari@aprilasia.com", display_name="Najiha Nur Hanna", roles=[Role.OPERATIONS_STAFF], team_id="team_docs_me"),
    UserRecord(id="u_ops_2", email="deswita_elvyani@aprilasia.com", display_name="Deswita Elvyani", roles=[Role.OPERATIONS_STAFF], team_id="team_docs_me"),
    UserRecord(id="u_ops_3", email="willy_ss@aprilasia.com", display_name="Willy Situmorang", roles=[Role.OPERATIONS_STAFF], team_id="team_docs_me"),
    UserRecord(id="u_ops_4", email="mitchelle_ting@aprilasia.com", display_name="Mitchelle Ting", roles=[Role.OPERATIONS_STAFF], team_id="team_docs_asia"),
    UserRecord(id="u_sup_1", email="hari_mardianto@aprilasia.com", display_name="Hari Mardianto", roles=[Role.SUPERVISOR], team_id="team_docs_me"),
    UserRecord(id="u_sup_2", email="eileen_teo@aprilasia.com", display_name="Teo Ei Leen", roles=[Role.SUPERVISOR], team_id="team_docs_asia"),
    UserRecord(id="u_admin_1", email="faraz_ali@aprilasia.com", display_name="Syed Faraz Ali", roles=[Role.ADMIN, Role.SUPERVISOR], team_id="team_docs_me"),
    UserRecord(id="u_audit_1", email="sokyong_ooi@aprilasia.com", display_name="Ooi Sok Yong", roles=[Role.AUDITOR], team_id="team_audit"),
]
DEFAULT_TEAMS = [
    {"id": "team_docs_me", "name": "Shipping Documentation - Middle East"},
    {"id": "team_docs_asia", "name": "Shipping Documentation - Asia"},
    {"id": "team_audit", "name": "Internal Audit"},
    {"id": "team_finance", "name": "Finance / Billing"},
]
DEFAULT_PARTIES = [
    PartyContact(id="p_algurg", name="Logistics Desk", email="logistics@algurg.ae", party_name="AL GURG STATIONERY LLC"),
    PartyContact(id="p_vital", name="Documentation", email="docs@vitalsolutions.sg", party_name="VITAL SOLUTIONS PTE. LTD."),
    PartyContact(id="p_safqa", name="Aziz Tejani", email="aziztz@safqa.co.ke", party_name="SAFQA LIMITED"),
    PartyContact(id="p_roxcel", name="Sales Desk", email="sales@roxcel.at", party_name="ROXCEL TRADING GMBH"),
    PartyContact(id="p_ifp", name="Export Team", email="exports@ifpla.com", party_name="INTERNATIONAL FOREST PRODUCTS LLC"),
    PartyContact(id="p_fujito", name="Nirmala Patwa", email="nirmala@fujitogrp.com", party_name="FUJITO GROUP (forwarder)"),
    PartyContact(id="p_psabdp", name="Chella Perumal", email="chella.perumal@psabdp.com", party_name="PSA BDP (forwarder)"),
    PartyContact(id="p_novakopa", name="Import Desk", email="import@novakopa.lt", party_name="UAB NOVAKOPA", approved=False),
]


class MemoryRepository(BaseRepository):
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.emails: dict[str, EmailMessage] = {}
        self.cases: dict[str, CaseRecord] = {}
        self.audit: list[AuditEvent] = []
        self.errors: list[ProcessingError] = []
        self.shares: dict[str, ShareRecord] = {}
        self.blobs: dict[str, bytes] = {}
        self.users: dict[str, UserRecord] = {u.id: u for u in DEFAULT_USERS}
        self.teams = list(DEFAULT_TEAMS)
        self.parties: dict[str, PartyContact] = {p.id: p for p in DEFAULT_PARTIES}
        self.policies: list[PolicyRecord] = [
            PolicyRecord(id="pol_default", version="v1", name="default", values=DEFAULT_POLICY, updated_by="system", updated_at=datetime.utcnow(), change_note="Initial policy")
        ]
        self.jobs: dict[str, dict[str, Any]] = {}
        self.credentials: dict[str, str] = {}      # user_id -> password hash
        self.revoked_sessions: set[str] = set()

    # emails
    def save_email(self, email: EmailMessage) -> None:
        with self._lock:
            self.emails[email.id] = email

    def get_email(self, email_id: str) -> Optional[EmailMessage]:
        return self.emails.get(email_id)

    def list_emails(self) -> list[EmailMessage]:
        return list(self.emails.values())

    def find_email_by_checksum(self, checksum: str) -> Optional[EmailMessage]:
        return next((e for e in self.emails.values() if e.checksum == checksum), None)

    def find_attachment_by_checksum(self, checksum: str) -> Optional[str]:
        for e in self.emails.values():
            for a in e.attachments:
                if a.checksum == checksum and a.size_bytes > 0:
                    return a.id
        return None

    def save_blob(self, pointer: str, data: bytes) -> None:
        self.blobs[pointer] = data

    def get_blob(self, pointer: str) -> Optional[bytes]:
        return self.blobs.get(pointer)

    # cases
    def save_case(self, case: CaseRecord) -> None:
        with self._lock:
            case.updated_at = datetime.utcnow()
            self.cases[case.id] = case

    def get_case(self, case_id: str) -> Optional[CaseRecord]:
        return self.cases.get(case_id)

    def get_case_by_email(self, email_id: str) -> Optional[CaseRecord]:
        return next((c for c in self.cases.values() if c.source_email_id == email_id), None)

    def list_cases(self) -> list[CaseRecord]:
        return list(self.cases.values())

    # audit / errors / shares
    def append_audit(self, event: AuditEvent) -> None:
        with self._lock:
            self.audit.append(event)  # append-only

    def append_audit_once(self, event: AuditEvent) -> bool:
        with self._lock:
            if any(existing.event_id == event.event_id for existing in self.audit):
                return False
            self.audit.append(event)
            return True

    def list_audit(self, case_id: Optional[str] = None) -> list[AuditEvent]:
        return [a for a in self.audit if case_id is None or a.case_id == case_id]

    def save_error(self, err: ProcessingError) -> None:
        self.errors.append(err)

    def save_share(self, share: ShareRecord) -> None:
        with self._lock:
            self.shares[share.id] = share

    def mark_share_confirming_if_pending(
        self,
        share_id: str,
        started_at: datetime,
    ) -> Optional[ShareRecord]:
        """Atomically claim a pending confirmation for recoverable processing."""
        with self._lock:
            share = self.shares.get(share_id)
            if not share or share.status != "PENDING_CONFIRMATION":
                return None
            share.status = "CONFIRMING"
            share.confirmation_started_at = started_at
            self.shares[share.id] = share
            return share.model_copy(deep=True)

    def complete_share_confirmation(
        self,
        share_id: str,
        actor_id: str,
    ) -> Optional[ShareRecord]:
        """Atomically apply case/audit effects and publish a confirmed share."""
        with self._lock:
            share = self.shares.get(share_id)
            if not share:
                return None
            if share.status == "SENT":
                return share.model_copy(deep=True)
            if share.status != "CONFIRMING":
                return None

            case = self.cases.get(share.case_id)
            if not case:
                return None
            case_snapshot = case.model_copy(deep=True)
            share_snapshot = share.model_copy(deep=True)
            audit_snapshot = list(self.audit)
            now = datetime.utcnow()
            target_status = (
                CaseStatus.AWAITING_RESPONSE
                if share.is_external
                else CaseStatus.ASSIGNED
            )
            send_action = (
                "NOTIFY_PARTY_SENT" if share.is_external else "SHARE_SENT"
            )
            before_status = case.status

            try:
                self.append_audit_once(
                    AuditEvent(
                        event_id=f"evt_{share.id}_sent",
                        case_id=case.id,
                        timestamp=now,
                        actor_type=ActorType.USER,
                        actor_id=actor_id,
                        action=send_action,
                        after={
                            "share_id": share.id,
                            "recipient_label": share.recipient_label,
                            "external": share.is_external,
                            "fields": [
                                field["field"]
                                for field in share.payload_preview.get("fields", [])
                            ],
                            "due_date": share.due_date,
                        },
                    )
                )
                if before_status != target_status:
                    self.append_audit_once(
                        AuditEvent(
                            event_id=f"evt_{share.id}_status",
                            case_id=case.id,
                            timestamp=now,
                            actor_type=ActorType.USER,
                            actor_id=actor_id,
                            action="STATUS_CHANGED",
                            before={"status": before_status.value},
                            after={"status": target_status.value},
                        )
                    )

                recipient_id = share.recipient_user_id or share.recipient_party_id
                if recipient_id and recipient_id not in case.shared_with:
                    case.shared_with.append(recipient_id)
                case.status = target_status
                self.save_case(case)
                share.status = "SENT"
                share.sent_at = now
                self.shares[share.id] = share
                return share.model_copy(deep=True)
            except Exception:
                self.cases[case.id] = case_snapshot
                self.shares[share.id] = share_snapshot
                self.audit[:] = audit_snapshot
                raise

    def list_shares(self, case_id: Optional[str] = None) -> list[ShareRecord]:
        return [s for s in self.shares.values() if case_id is None or s.case_id == case_id]

    def get_share(self, share_id: str) -> Optional[ShareRecord]:
        return self.shares.get(share_id)

    # users / parties / policy
    def list_users(self) -> list[UserRecord]:
        return list(self.users.values())

    def get_user(self, user_id: str) -> Optional[UserRecord]:
        return self.users.get(user_id)

    def save_user(self, user: UserRecord) -> None:
        with self._lock:
            self.users[user.id] = user

    def get_password_hash(self, user_id: str) -> Optional[str]:
        return self.credentials.get(user_id)

    def set_password_hash(self, user_id: str, password_hash: str) -> None:
        with self._lock:
            self.credentials[user_id] = password_hash

    def revoke_session(self, session_id: str) -> None:
        self.revoked_sessions.add(session_id)

    def is_session_revoked(self, session_id: str) -> bool:
        return session_id in self.revoked_sessions

    def list_parties(self) -> list[PartyContact]:
        return list(self.parties.values())

    def get_party(self, party_id: str) -> Optional[PartyContact]:
        return self.parties.get(party_id)

    def get_active_policy(self) -> PolicyRecord:
        return self.policies[-1]

    def save_policy_version(self, policy: PolicyRecord) -> None:
        self.policies.append(policy)

    def list_policy_versions(self) -> list[PolicyRecord]:
        return list(self.policies)

    # idempotency
    def seen_job(self, job_key: str) -> bool:
        return job_key in self.jobs

    def mark_job(self, job_key: str, result: dict[str, Any]) -> None:
        self.jobs[job_key] = result

    # ---- snapshot helpers (used by seed export) ---------------------------
    def dump(self) -> dict[str, Any]:
        return {
            "emails": [e.model_dump(mode="json") for e in self.emails.values()],
            "cases": [c.model_dump(mode="json") for c in self.cases.values()],
            "audit": [a.model_dump(mode="json") for a in self.audit],
            "errors": [e.model_dump(mode="json") for e in self.errors],
            "shares": [s.model_dump(mode="json") for s in self.shares.values()],
            "users": [u.model_dump(mode="json") for u in self.users.values()],
            "teams": self.teams,
            "parties": [p.model_dump(mode="json") for p in self.parties.values()],
            "policies": [p.model_dump(mode="json") for p in self.policies],
            "credentials": dict(self.credentials),
        }

    def load(self, snapshot: dict[str, Any]) -> None:
        with self._lock:
            self.emails = {e["id"]: EmailMessage(**e) for e in snapshot.get("emails", [])}
            self.cases = {c["id"]: CaseRecord(**c) for c in snapshot.get("cases", [])}
            self.audit = [AuditEvent(**a) for a in snapshot.get("audit", [])]
            self.errors = [ProcessingError(**e) for e in snapshot.get("errors", [])]
            self.shares = {s["id"]: ShareRecord(**s) for s in snapshot.get("shares", [])}
            if snapshot.get("users"):
                self.users = {u["id"]: UserRecord(**u) for u in snapshot["users"]}
            if snapshot.get("parties"):
                self.parties = {p["id"]: PartyContact(**p) for p in snapshot["parties"]}
            if snapshot.get("policies"):
                self.policies = [PolicyRecord(**p) for p in snapshot["policies"]]
            if snapshot.get("credentials"):
                self.credentials = dict(snapshot["credentials"])

    def load_file(self, path: str | Path) -> None:
        p = Path(path)
        if p.exists():
            self.load(json.loads(p.read_text(encoding="utf-8")))

    def save_file(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.dump(), indent=1, default=str), encoding="utf-8")
