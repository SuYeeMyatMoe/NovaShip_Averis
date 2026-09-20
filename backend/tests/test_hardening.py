"""Fail-closed P3 security, transport, persistence, and configuration regressions."""
from __future__ import annotations

import copy
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("REPO_BACKEND", "memory")
os.environ["AUTO_SEED"] = "0"
os.environ["AUTH_MODE"] = "demo"
os.environ["EMAIL_SEND_MODE"] = "simulate"
os.environ["VECTOR_STORE"] = "local"
os.environ["EMBEDDING_PROVIDER"] = "local"
os.environ["LANGGRAPH_CHECKPOINT"] = "memory"

from app.agents.graph import _make_checkpointer  # noqa: E402
from app.agents.rag import RAG  # noqa: E402
from app.api import auth_routes  # noqa: E402
from app.auth import accounts, rbac  # noqa: E402
from app.config import ConfigurationError, cors_allowed_origins, get_repo, supabase_server_credentials  # noqa: E402
from app.connectors.email_connectors import GmailConnector  # noqa: E402
from app.contracts.schemas import DraftDecision, DraftStatus, Role, ShareRecord, ShareRequest, RecipientType  # noqa: E402
from app.file_security import UnsafeUpload, safe_filename, validate_attachment_count, validate_file_size  # noqa: E402
from app.main import app  # noqa: E402
from app.repositories.base import StorageAuthorizationError, StorageProviderError  # noqa: E402
from app.repositories.memory import MemoryRepository  # noqa: E402
from app.repositories.supabase_repo import SupabaseRepository  # noqa: E402
from app.services.case_service import CaseService  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
client = TestClient(app)


def _gmail_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMAIL_SEND_MODE", "gmail")
    monkeypatch.setenv("GMAIL_CLIENT_ID", "client")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "not-a-real-secret")
    monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "not-a-real-refresh-token")
    monkeypatch.setenv("GMAIL_ADDRESS", "mailbox@example.com")


def _service_with_draft():
    repo = MemoryRepository()
    repo.load_file(ROOT / "supabase" / "seed" / "snapshot.json")
    case = next(case for case in repo.list_cases() if case.drafts and case.drafts[0].requires_external_approval)
    case = case.model_copy(deep=True)
    case.id = f"{case.id}_hardening"
    case.drafts[0].id = "draft_hardening"
    case.drafts[0].to = ["recipient@example.com"]
    case.drafts[0].status = DraftStatus.PROPOSED
    repo.save_case(case)
    return repo, CaseService(repo), case, repo.get_user("u_sup_1")


def test_jwt_unmapped_rejected_and_mapped_roles_are_exact(monkeypatch):
    repo = get_repo()
    user = repo.get_user("u_admin_1")
    old_subject = user.auth_user_id
    monkeypatch.setenv("AUTH_MODE", "jwt")
    monkeypatch.setattr(rbac, "_jwt_claims", lambda _token: {"sub": "00000000-0000-0000-0000-000000000001"})
    assert client.get("/me", headers={"Authorization": "Bearer valid.jwt.value"}).status_code == 401
    user.auth_user_id = "00000000-0000-0000-0000-000000000002"
    monkeypatch.setattr(rbac, "_jwt_claims", lambda _token: {"sub": "00000000-0000-0000-0000-000000000002"})
    response = client.get("/me", headers={"Authorization": "Bearer valid.jwt.value"})
    assert response.status_code == 200
    assert response.json()["roles"] == [role.value for role in user.roles]
    user.auth_user_id = old_subject


def test_jwt_mode_rejects_local_token_header_login_and_register(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "demo")
    token, _ = accounts.issue_token("u_ops_1")
    monkeypatch.setenv("AUTH_MODE", "jwt")
    assert client.get("/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert client.get("/me", headers={"X-User-Id": "u_ops_1"}).status_code == 401
    assert client.post("/auth/login", json={"email": "x@example.com", "password": "anything"}).status_code == 403
    assert client.post("/auth/register", json={"email": "x@example.com", "password": "Secret123!", "display_name": "Test User"}).status_code == 403
    monkeypatch.setenv("AUTH_MODE", "local")
    monkeypatch.setenv("SESSION_SECRET", "a-strong-local-session-secret-with-32-chars")
    assert client.post("/auth/register", json={"email": "x@example.com", "password": "Secret123!", "display_name": "Test User"}).status_code == 403


def test_jwks_verification_keeps_signature_issuer_and_audience_checks(monkeypatch):
    import jwt

    calls = {}
    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.setenv("SUPABASE_JWT_ALGORITHM", "JWKS")
    monkeypatch.setattr(rbac, "_jwks_client", lambda _url: type("JWKS", (), {"get_signing_key_from_jwt": lambda self, _token: type("Key", (), {"key": "public-key"})()})())

    def decode(token, key, **kwargs):
        calls.update({"token": token, "key": key, **kwargs})
        return {"sub": "00000000-0000-0000-0000-000000000003"}

    monkeypatch.setattr(jwt, "decode", decode)
    assert rbac._jwt_claims("header.payload.signature")["sub"].endswith("3")
    assert calls["algorithms"] == ["RS256", "ES256"]
    assert calls["audience"] == "authenticated"
    assert calls["issuer"] == "https://project.supabase.co/auth/v1"
    assert set(calls["options"]["require"]) == {"exp", "iat", "sub", "aud", "iss"}


def test_demo_only_seeding_and_local_secret_fail_closed(monkeypatch):
    repo = MemoryRepository()
    monkeypatch.setattr(auth_routes, "get_repo", lambda: repo)
    monkeypatch.setenv("AUTH_MODE", "local")
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    assert auth_routes.seed_demo_credentials() == 0
    assert repo.credentials == {}
    with pytest.raises(ConfigurationError, match="SESSION_SECRET"):
        accounts.session_secret()
    monkeypatch.setenv("SESSION_SECRET", "change-me-in-production")
    with pytest.raises(ConfigurationError, match="SESSION_SECRET"):
        accounts.session_secret()


def test_auth_audit_ids_are_unique_without_listing_audit(monkeypatch):
    repo = MemoryRepository()
    monkeypatch.setattr(auth_routes, "get_repo", lambda: repo)
    repo.list_audit = lambda *_: (_ for _ in ()).throw(AssertionError("must not list audit"))  # type: ignore[method-assign]
    auth_routes._audit("u_ops_1", "LOGIN")
    auth_routes._audit("u_ops_1", "LOGIN")
    assert len(repo.audit) == 2
    assert repo.audit[0].event_id != repo.audit[1].event_id


def test_auditor_is_forbidden_from_every_case_mutation():
    response = client.post("/webhooks/email", headers={"X-User-Id": "u_ops_1"}, json={
        "email_id": "hardening_auditor_case", "from": "sender@example.com", "subject": "information", "body": "No action required", "attachments": []
    })
    case_id = response.json()["id"]
    headers = {"X-User-Id": "u_audit_1"}
    assert client.post(f"/cases/{case_id}/no-action", headers=headers).status_code == 403
    assert client.post(f"/cases/{case_id}/complete", headers=headers).status_code == 403
    assert client.post(f"/cases/{case_id}/request-review", headers=headers).status_code == 403


def test_share_acknowledgement_is_recipient_bound():
    repo = get_repo()
    share = ShareRecord(id="share_recipient_guard", case_id="case_guard", shared_by="u_ops_1", recipient_type=RecipientType.INTERNAL_USER,
                        recipient_user_id="u_sup_1", recipient_label="Supervisor", message="Review this")
    repo.save_share(share)
    assert client.post(f"/shares/{share.id}/acknowledge", headers={"X-User-Id": "u_ops_1"}).status_code == 403
    assert client.post(f"/shares/{share.id}/acknowledge", headers={"X-User-Id": "u_sup_1"}).status_code == 200


def test_simulation_never_calls_gmail_and_is_explicit(monkeypatch):
    _repo, service, case, supervisor = _service_with_draft()
    monkeypatch.setenv("EMAIL_SEND_MODE", "simulate")
    monkeypatch.setattr(GmailConnector, "send", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Gmail called")))
    result = service.approve_draft(case.id, DraftDecision(draft_id="draft_hardening"), supervisor)
    assert result.drafts[0].status.value == "SIMULATED"
    assert any(event.action == "NOTIFICATION_SIMULATED" for event in service.repo.list_audit(case.id))


def test_gmail_success_failure_and_sent_idempotency(monkeypatch):
    _gmail_env(monkeypatch)
    repo, service, case, supervisor = _service_with_draft()
    calls = []
    monkeypatch.setattr(GmailConnector, "send", lambda *_args, **_kwargs: calls.append(True) or {"status": 200, "id": "gmail-1"})
    first = service.approve_draft(case.id, DraftDecision(draft_id="draft_hardening"), supervisor)
    assert first.drafts[0].status.value == "SENT"
    service.approve_draft(case.id, DraftDecision(draft_id="draft_hardening"), supervisor)
    assert len(calls) == 1

    repo2, service2, case2, supervisor2 = _service_with_draft()
    monkeypatch.delenv("GMAIL_CLIENT_ID")
    with pytest.raises(Exception) as caught:
        service2.approve_draft(case2.id, DraftDecision(draft_id="draft_hardening"), supervisor2)
    assert getattr(caught.value, "status_code", None) == 502
    failed = repo2.get_case(case2.id)
    assert failed.drafts[0].status.value == "SEND_FAILED"
    assert any(error.category.value == "NOTIFICATION_ERROR" and error.retryable for error in failed.errors)


def test_same_share_id_and_custom_message_survive_confirmation(monkeypatch):
    _gmail_env(monkeypatch)
    monkeypatch.setattr(GmailConnector, "send", lambda *_args, **_kwargs: {"status": 200, "id": "gmail-share"})
    repo, service, case, supervisor = _service_with_draft()
    custom = "Custom reviewed message"
    preview = service.share(case.id, ShareRequest(recipient_type=RecipientType.NOTIFY_PARTY_CONTACT, recipient_party_id="p_safqa",
                                                 message=custom, preview_only=True), supervisor)
    original = copy.deepcopy(preview["share"])
    sent = service.confirm_share(case.id, original["id"], supervisor)
    assert sent["share"]["id"] == original["id"]
    assert sent["share"]["status"] == "SENT"
    assert sent["share"]["message"] == original["message"]
    assert sent["share"]["message"].startswith(custom)
    assert len(repo.list_shares(case.id)) == 1


class _GmailUnauthorized(Exception):
    resp = type("Resp", (), {"status": 401})()


def test_gmail_token_expiry_and_401_refresh(monkeypatch):
    _gmail_env(monkeypatch)
    tokens = iter(["token-one", "token-two"])

    def refresh(credentials, _request):
        credentials.token = next(tokens)
        credentials.expiry = datetime.now(timezone.utc) + timedelta(seconds=100)

    monkeypatch.setattr("app.connectors.email_connectors.GoogleCredentials.refresh", refresh)
    now = [0.0]
    monkeypatch.setattr("app.connectors.email_connectors.time.monotonic", lambda: now[0])
    connector = GmailConnector()
    assert connector.token() == "token-one"
    now[0] = 95.0
    assert connector.token() == "token-two"

    attempts = []
    invalidations = []
    monkeypatch.setattr(connector, "_service_client", lambda: object())
    monkeypatch.setattr(connector, "_invalidate_token", lambda: invalidations.append(True))

    class Request:
        def execute(self):
            attempts.append(True)
            if len(attempts) == 1:
                raise _GmailUnauthorized()
            return {"id": "gmail-sent"}

    result = connector._execute(lambda _service: Request())
    assert result["id"] == "gmail-sent"
    assert len(attempts) == 2
    assert len(invalidations) == 1


def test_supabase_server_secret_is_required_without_public_fallback(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.setenv("SUPABASE_ANON_KEY", "public-key-must-not-be-used")
    with pytest.raises(ConfigurationError, match="SUPABASE_SECRET_KEY"):
        supabase_server_credentials()


class _StorageBucket:
    def __init__(self, exc):
        self.exc = exc

    def download(self, _pointer):
        raise self.exc

    def upload(self, *_args):
        raise self.exc

    def create_signed_url(self, *_args):
        raise self.exc


class _Storage:
    def __init__(self, exc):
        self.exc = exc

    def from_(self, _bucket):
        return _StorageBucket(self.exc)


def _storage_repo(exc):
    repo = SupabaseRepository.__new__(SupabaseRepository)
    repo.bucket = "documents"
    repo.client = type("Client", (), {"storage": _Storage(exc)})()
    return repo


def test_storage_not_found_is_distinct_and_failures_surface():
    missing = Exception({"statusCode": 404, "message": "Object not found"})
    assert _storage_repo(missing).get_blob("missing") is None
    outage = Exception({"statusCode": 503, "message": "upstream unavailable"})
    with pytest.raises(StorageProviderError):
        _storage_repo(outage).get_blob("object")
    with pytest.raises(StorageProviderError):
        _storage_repo(outage).signed_url("object")
    forbidden = Exception({"statusCode": 403, "message": "Forbidden"})
    with pytest.raises(StorageAuthorizationError):
        _storage_repo(forbidden).save_blob("object", b"data")


@pytest.mark.parametrize("name", ["../evil.pdf", "nested/../../evil.pdf", r"C:\\temp\\evil.pdf", "/tmp/evil.pdf"])
def test_unsafe_upload_names_are_rejected(name):
    with pytest.raises(UnsafeUpload):
        safe_filename(name)


def test_valid_upload_basename_is_sanitized_and_limits_apply(monkeypatch):
    assert safe_filename("shipping instruction (final).pdf") == "shipping instruction _final_.pdf"
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "4")
    monkeypatch.setenv("MAX_ATTACHMENT_COUNT", "2")
    validate_file_size(4)
    validate_attachment_count(2)
    with pytest.raises(UnsafeUpload):
        validate_file_size(5)
    with pytest.raises(UnsafeUpload):
        validate_attachment_count(3)


class _TableResult:
    data = []


class _FakeTable:
    def __init__(self, database, name):
        self.database, self.name = database, name
        self.action, self.payload, self.filters = None, None, []

    def upsert(self, payload):
        self.action, self.payload = "upsert", payload
        return self

    def delete(self):
        self.action = "delete"
        return self

    def eq(self, field, value):
        self.filters.append((field, value))
        return self

    def execute(self):
        rows = self.database.setdefault(self.name, {})
        if self.action == "delete":
            for row_id, row in list(rows.items()):
                if all(row.get(field) == value for field, value in self.filters):
                    del rows[row_id]
        elif self.action == "upsert":
            payloads = self.payload if isinstance(self.payload, list) else [self.payload]
            for row in payloads:
                rows[row["id"]] = dict(row)
        return _TableResult()


class _FakeClient:
    def __init__(self):
        self.database = {}

    def table(self, name):
        return _FakeTable(self.database, name)


def test_save_case_removes_stale_normalized_children():
    source = MemoryRepository()
    source.load_file(ROOT / "supabase" / "seed" / "snapshot.json")
    case = next(case.model_copy(deep=True) for case in source.list_cases() if case.comparison and case.drafts)
    repo = SupabaseRepository.__new__(SupabaseRepository)
    repo.client, repo.tenant, repo.bucket = _FakeClient(), "tenant_april", "documents"
    repo.save_case(case)
    assert repo.client.database["comparison_fields"] and repo.client.database["drafts"]
    case.comparison = None
    case.si_extraction = case.bl_extraction = None
    case.summary = case.recommendation = None
    case.drafts = []
    case.assigned_user_id = case.assigned_team_id = None
    repo.save_case(case)
    for table in ("comparisons", "comparison_fields", "extracted_fields", "case_summaries", "action_recommendations", "drafts", "assignments"):
        assert not any(row.get("case_id") == case.id for row in repo.client.database.get(table, {}).values())


def test_postgres_checkpoint_selection_never_silently_falls_back(monkeypatch):
    monkeypatch.setenv("LANGGRAPH_CHECKPOINT", "postgres")
    monkeypatch.delenv("LANGGRAPH_PG_URL", raising=False)
    with pytest.raises(ConfigurationError, match="LANGGRAPH_PG_URL"):
        _make_checkpointer()
    monkeypatch.setenv("LANGGRAPH_PG_URL", "postgresql://invalid")
    monkeypatch.setitem(__import__("sys").modules, "langgraph.checkpoint.postgres", None)
    with pytest.raises(ConfigurationError, match="initialization failed"):
        _make_checkpointer()


def test_rag_supabase_credentials_and_dimensions_are_validated(monkeypatch):
    monkeypatch.setenv("VECTOR_STORE", "supabase")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local")
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "256")
    monkeypatch.setenv("SUPABASE_VECTOR_DIMENSIONS", "768")
    with pytest.raises(ConfigurationError, match="vector dimension"):
        RAG()
    monkeypatch.setenv("SUPABASE_VECTOR_DIMENSIONS", "256")
    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.setenv("SUPABASE_ANON_KEY", "public")
    with pytest.raises(ConfigurationError, match="SUPABASE_SECRET_KEY"):
        RAG()


def test_scoped_rag_search_filters_before_vector_ranking():
    migration = (
        Path(__file__).resolve().parents[2]
        / "supabase"
        / "migrations"
        / "0005_rag_scoped_search.sql"
    ).read_text(encoding="utf-8").lower()
    assert "with filtered as materialized" in migration
    assert "p_case_id is null or e.case_id is null or e.case_id = p_case_id" in migration
    assert migration.index("with filtered as materialized") < migration.index("order by e.embedding")


def test_production_cors_requires_explicit_non_wildcard_origins(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    with pytest.raises(ConfigurationError, match="CORS_ORIGINS"):
        cors_allowed_origins()
    monkeypatch.setenv("CORS_ORIGINS", "*")
    with pytest.raises(ConfigurationError, match="wildcard"):
        cors_allowed_origins()
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example.com")
    assert cors_allowed_origins() == ["https://app.example.com"]
