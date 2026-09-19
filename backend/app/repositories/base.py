"""
Repository contract. The backend owns ALL persistence; AI nodes never touch
the database. Two implementations:
  * MemoryRepository   - fixtures/seed JSON (Day 19, tests, offline demo)
  * SupabaseRepository - PostgREST via supabase-py (Day 20+)
Selected by REPO_BACKEND=memory|supabase.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Optional

from app.contracts.schemas import (
    AuditEvent,
    CaseRecord,
    EmailMessage,
    PartyContact,
    PolicyRecord,
    ProcessingError,
    ShareRecord,
    UserRecord,
)


class BaseRepository(ABC):
    # ---- emails -----------------------------------------------------------
    @abstractmethod
    def save_email(self, email: EmailMessage) -> None: ...
    @abstractmethod
    def get_email(self, email_id: str) -> Optional[EmailMessage]: ...
    @abstractmethod
    def list_emails(self) -> list[EmailMessage]: ...
    @abstractmethod
    def find_email_by_checksum(self, checksum: str) -> Optional[EmailMessage]: ...
    @abstractmethod
    def find_attachment_by_checksum(self, checksum: str) -> Optional[str]: ...
    @abstractmethod
    def save_blob(self, pointer: str, data: bytes) -> None: ...
    @abstractmethod
    def get_blob(self, pointer: str) -> Optional[bytes]: ...

    # ---- cases ------------------------------------------------------------
    @abstractmethod
    def save_case(self, case: CaseRecord) -> None: ...
    @abstractmethod
    def get_case(self, case_id: str) -> Optional[CaseRecord]: ...
    @abstractmethod
    def get_case_by_email(self, email_id: str) -> Optional[CaseRecord]: ...
    @abstractmethod
    def list_cases(self) -> list[CaseRecord]: ...

    # ---- audit / errors / shares -----------------------------------------
    @abstractmethod
    def append_audit(self, event: AuditEvent) -> None: ...
    @abstractmethod
    def list_audit(self, case_id: Optional[str] = None) -> list[AuditEvent]: ...
    @abstractmethod
    def save_error(self, err: ProcessingError) -> None: ...
    @abstractmethod
    def save_share(self, share: ShareRecord) -> None: ...
    @abstractmethod
    def mark_share_sent_if_pending(
        self,
        share_id: str,
        sent_at: datetime,
    ) -> Optional[ShareRecord]: ...
    @abstractmethod
    def list_shares(self, case_id: Optional[str] = None) -> list[ShareRecord]: ...
    @abstractmethod
    def get_share(self, share_id: str) -> Optional[ShareRecord]: ...

    # ---- users / parties / policy ----------------------------------------
    @abstractmethod
    def list_users(self) -> list[UserRecord]: ...
    @abstractmethod
    def get_user(self, user_id: str) -> Optional[UserRecord]: ...
    @abstractmethod
    def list_parties(self) -> list[PartyContact]: ...
    @abstractmethod
    def get_party(self, party_id: str) -> Optional[PartyContact]: ...
    @abstractmethod
    def get_active_policy(self) -> PolicyRecord: ...
    @abstractmethod
    def save_policy_version(self, policy: PolicyRecord) -> None: ...
    @abstractmethod
    def list_policy_versions(self) -> list[PolicyRecord]: ...

    # ---- accounts (login / register / logout) ------------------------------
    # Default implementations keep credentials and revoked sessions in process
    # memory so any backend supports login; MemoryRepository persists them in
    # its snapshot, SupabaseRepository stores them in `user_credentials`.
    def get_user_by_email(self, email: str) -> Optional[UserRecord]:
        target = (email or "").strip().lower()
        return next((u for u in self.list_users() if u.email.lower() == target), None)

    def save_user(self, user: UserRecord) -> None:
        raise NotImplementedError("this repository does not support self-registration")

    def get_password_hash(self, user_id: str) -> Optional[str]:
        return self.__dict__.setdefault("_credentials", {}).get(user_id)

    def set_password_hash(self, user_id: str, password_hash: str) -> None:
        self.__dict__.setdefault("_credentials", {})[user_id] = password_hash

    def revoke_session(self, session_id: str) -> None:
        self.__dict__.setdefault("_revoked_sessions", set()).add(session_id)

    def is_session_revoked(self, session_id: str) -> bool:
        return session_id in self.__dict__.setdefault("_revoked_sessions", set())

    # ---- idempotency ------------------------------------------------------
    @abstractmethod
    def seen_job(self, job_key: str) -> bool: ...
    @abstractmethod
    def mark_job(self, job_key: str, result: dict[str, Any]) -> None: ...
