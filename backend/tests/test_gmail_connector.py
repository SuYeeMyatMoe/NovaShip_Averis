"""Gmail OAuth, MIME normalization, attachment, and approved-send regressions."""
from __future__ import annotations

import base64
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from email import policy
from email.parser import BytesParser
from pathlib import Path
import threading

import pytest

import app.connectors.email_connectors as email_connectors
from app.config import ConfigurationError
from app.connectors.email_connectors import GmailConnector
from app.contracts.schemas import DraftDecision, DraftStatus, RecipientType, ShareRequest
from app.file_security import UnsafeUpload
from app.pipeline.orchestrator import Pipeline
from app.repositories.memory import MemoryRepository
from app.services.case_service import CaseService

ROOT = Path(__file__).resolve().parents[2]


def _gmail_env(monkeypatch: pytest.MonkeyPatch, *, send: bool = False) -> None:
    monkeypatch.setenv("GMAIL_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "test-refresh-token")
    monkeypatch.setenv("GMAIL_ADDRESS", "documentation@example.com")
    if send:
        monkeypatch.setenv("EMAIL_SEND_MODE", "gmail")


def _encoded(data: bytes | str) -> str:
    raw = data.encode() if isinstance(data, str) else data
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


class _Request:
    def __init__(self, result=None, error: Exception | None = None):
        self.result, self.error = result, error

    def execute(self):
        if self.error:
            raise self.error
        return self.result


class _GmailService:
    def __init__(self, listed=None, messages=None, attachments=None, sent=None):
        self.listed = listed or {"messages": []}
        self.message_map = messages or {}
        self.attachment_map = attachments or {}
        self.sent = sent or {"id": "gmail-sent-id", "threadId": "thread-sent"}
        self.sent_body = None
        self.list_kwargs = None

    def users(self):
        return self

    def messages(self):
        return self

    def attachments(self):
        return self

    def list(self, **kwargs):
        self.list_kwargs = kwargs
        return _Request(self.listed)

    def get(self, **kwargs):
        if "messageId" in kwargs:
            return _Request(self.attachment_map[kwargs["id"]])
        return _Request(self.message_map[kwargs["id"]])

    def send(self, **kwargs):
        self.sent_body = kwargs["body"]
        return _Request(self.sent)


def _message_payload(*, html_only: bool = False, attachments: list[dict] | None = None) -> dict:
    body_parts = ([{
        "mimeType": "text/html",
        "filename": "",
        "headers": [{"name": "Content-Type", "value": "text/html; charset=UTF-8"}],
        "body": {"data": _encoded("<p>Hello &amp; welcome</p><script>ignore()</script><div>Second line</div>")},
    }] if html_only else [{
        "mimeType": "multipart/alternative",
        "filename": "",
        "parts": [
            {"mimeType": "text/plain", "filename": "", "headers": [{"name": "Content-Type", "value": "text/plain; charset=UTF-8"}], "body": {"data": _encoded("Plain message body")}},
            {"mimeType": "text/html", "filename": "", "headers": [], "body": {"data": _encoded("<p>HTML fallback</p>")}},
        ],
    }])
    return {
        "id": "gmail-message-1",
        "threadId": "gmail-thread-1",
        "internalDate": "1760000000000",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [
                {"name": "Message-ID", "value": "<stable-id@example.com>"},
                {"name": "From", "value": "Sender Name <sender@example.com>"},
                {"name": "To", "value": "One <one@example.com>, two@example.com"},
                {"name": "Cc", "value": "copy@example.com"},
                {"name": "Subject", "value": "Shipping documents"},
            ],
            "parts": body_parts + (attachments or []),
        },
    }


def _connector_with_service(monkeypatch: pytest.MonkeyPatch, service: _GmailService) -> GmailConnector:
    _gmail_env(monkeypatch)
    connector = GmailConnector()
    monkeypatch.setattr(connector, "_service_client", lambda: service)
    return connector


class _FakeCredentials:
    instances: list["_FakeCredentials"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.token = None
        self.expiry = None
        self.refresh_count = 0
        self.__class__.instances.append(self)

    def refresh(self, _request):
        self.refresh_count += 1
        self.token = f"access-token-{self.refresh_count}"
        self.expiry = datetime.utcnow() + timedelta(seconds=100)


def test_gmail_refresh_token_exchange_and_expiry_cache(monkeypatch):
    _gmail_env(monkeypatch)
    _FakeCredentials.instances.clear()
    now = [0.0]
    monkeypatch.setattr(email_connectors, "GoogleCredentials", _FakeCredentials)
    monkeypatch.setattr(email_connectors, "GoogleAuthRequest", lambda: object())
    monkeypatch.setattr(email_connectors.time, "monotonic", lambda: now[0])
    connector = GmailConnector()
    assert connector.token() == "access-token-1"
    assert connector.token() == "access-token-1"
    credentials = _FakeCredentials.instances[-1]
    assert credentials.refresh_count == 1
    assert credentials.kwargs["refresh_token"] == "test-refresh-token"
    assert set(credentials.kwargs["scopes"]) == set(GmailConnector.SCOPES)
    now[0] = 95.0
    assert connector.token() == "access-token-2"
    assert credentials.refresh_count == 2


def test_gmail_401_refreshes_and_retries_once(monkeypatch):
    _gmail_env(monkeypatch)
    _FakeCredentials.instances.clear()
    monkeypatch.setattr(email_connectors, "GoogleCredentials", _FakeCredentials)
    monkeypatch.setattr(email_connectors, "GoogleAuthRequest", lambda: object())
    monkeypatch.setattr(email_connectors, "google_api_build", lambda *_args, **_kwargs: object())
    connector = GmailConnector()
    calls = [0]

    class Unauthorized(Exception):
        resp = type("Response", (), {"status": 401})()

    def factory(_service):
        calls[0] += 1
        return _Request({"ok": True}, Unauthorized("expired") if calls[0] == 1 else None)

    assert connector._execute(factory) == {"ok": True}
    assert calls[0] == 2
    assert _FakeCredentials.instances[-1].refresh_count == 2


def test_gmail_inbox_normalizes_multipart_and_attachment(monkeypatch):
    attachment = {"mimeType": "application/pdf", "filename": "draft bill.pdf", "body": {"attachmentId": "att-1", "size": 7}}
    message = _message_payload(attachments=[attachment])
    service = _GmailService(
        listed={"messages": [{"id": message["id"], "threadId": message["threadId"]}]},
        messages={message["id"]: message},
        attachments={"att-1": {"size": 7, "data": _encoded(b"PDFDATA")}},
    )
    connector = _connector_with_service(monkeypatch, service)
    since = datetime(2025, 1, 2, 3, 4, 5)
    items = list(connector.fetch(since=since, limit=3))
    assert len(items) == 1
    item = items[0]
    assert item.provider == "gmail"
    assert item.raw["provider_message_id"] == "gmail-message-1"
    assert item.raw["conversation_id"] == "gmail-thread-1"
    assert item.raw["from"] == "sender@example.com" and item.raw["from_name"] == "Sender Name"
    assert item.raw["to"] == ["one@example.com", "two@example.com"]
    assert item.raw["cc"] == ["copy@example.com"]
    assert item.raw["subject"] == "Shipping documents"
    assert item.raw["body"] == "Plain message body"
    assert item.raw["attachments"] == ["attachments/draft bill.pdf"]
    assert item.blobs["attachments/draft bill.pdf"] == b"PDFDATA"
    assert service.list_kwargs["labelIds"] == ["INBOX"] and service.list_kwargs["maxResults"] == 3
    assert service.list_kwargs["q"].startswith("after:")


def test_gmail_html_body_fallback_is_text_only(monkeypatch):
    message = _message_payload(html_only=True)
    service = _GmailService(listed={"messages": [{"id": message["id"]}]}, messages={message["id"]: message})
    item = list(_connector_with_service(monkeypatch, service).fetch(limit=1))[0]
    assert "Hello & welcome" in item.raw["body"]
    assert "Second line" in item.raw["body"]
    assert "ignore()" not in item.raw["body"] and "<" not in item.raw["body"]


@pytest.mark.parametrize(
    ("env_name", "env_value", "attachments"),
    [
        ("MAX_ATTACHMENT_COUNT", "1", [
            {"mimeType": "application/pdf", "filename": "one.pdf", "body": {"data": _encoded(b"1"), "size": 1}},
            {"mimeType": "application/pdf", "filename": "two.pdf", "body": {"data": _encoded(b"2"), "size": 1}},
        ]),
        ("MAX_UPLOAD_BYTES", "4", [
            {"mimeType": "application/pdf", "filename": "large.pdf", "body": {"attachmentId": "large", "size": 5}},
        ]),
        ("MAX_UPLOAD_BYTES", "100", [
            {"mimeType": "application/pdf", "filename": "../unsafe.pdf", "body": {"data": _encoded(b"safe"), "size": 4}},
        ]),
    ],
)
def test_gmail_attachment_limits_and_safe_filename(monkeypatch, env_name, env_value, attachments):
    monkeypatch.setenv(env_name, env_value)
    message = _message_payload(attachments=attachments)
    service = _GmailService(listed={"messages": [{"id": message["id"]}]}, messages={message["id"]: message})
    with pytest.raises(UnsafeUpload):
        list(_connector_with_service(monkeypatch, service).fetch(limit=1))


def test_gmail_send_builds_rfc2822_base64url_message(monkeypatch):
    service = _GmailService()
    connector = _connector_with_service(monkeypatch, service)
    result = connector.send(["to@example.com"], "Approved subject", "Exact approved body", ["copy@example.com"])
    decoded = base64.urlsafe_b64decode(service.sent_body["raw"])
    message = BytesParser(policy=policy.default).parsebytes(decoded)
    assert result["id"] == "gmail-sent-id"
    assert message["From"] == "documentation@example.com"
    assert message["To"] == "to@example.com" and message["Cc"] == "copy@example.com"
    assert message["Subject"] == "Approved subject"
    assert message.get_body(preferencelist=("plain",)).get_content().strip() == "Exact approved body"


def test_gmail_duplicate_replay_does_not_create_second_case(monkeypatch):
    message = _message_payload()
    service = _GmailService(listed={"messages": [{"id": message["id"]}]}, messages={message["id"]: message})
    inbound = list(_connector_with_service(monkeypatch, service).fetch(limit=1))[0]
    repo = MemoryRepository()
    pipeline = Pipeline(repo)
    first_email = pipeline.ingest_email(inbound.raw, inbound.blobs, provider=inbound.provider, received_at=inbound.received_at)
    first_case = pipeline.run(first_email)
    second_email = pipeline.ingest_email(inbound.raw, inbound.blobs, provider=inbound.provider, received_at=inbound.received_at)
    second_case = pipeline.run(second_email)
    assert first_case.id == second_case.id
    assert len(repo.list_cases()) == 1


def _service_with_draft():
    repo = MemoryRepository()
    repo.load_file(ROOT / "supabase" / "seed" / "snapshot.json")
    case = next(case.model_copy(deep=True) for case in repo.list_cases() if case.drafts and case.drafts[0].requires_external_approval)
    case.id = f"{case.id}_gmail"
    case.drafts[0].id = "draft_gmail"
    case.drafts[0].to = ["recipient@example.com"]
    case.drafts[0].status = DraftStatus.PROPOSED
    repo.save_case(case)
    return repo, CaseService(repo), case, repo.get_user("u_sup_1")


def test_simulation_never_calls_gmail(monkeypatch):
    _repo, service, case, supervisor = _service_with_draft()
    monkeypatch.setenv("EMAIL_SEND_MODE", "simulate")
    fail = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network provider called"))
    monkeypatch.setattr(GmailConnector, "send", fail)
    result = service.approve_draft(case.id, DraftDecision(draft_id="draft_gmail"), supervisor)
    assert result.drafts[0].status == DraftStatus.SIMULATED
    service.approve_draft(case.id, DraftDecision(draft_id="draft_gmail"), supervisor)
    assert result.drafts[0].status == DraftStatus.SIMULATED


def test_simulated_draft_sends_when_mode_switches_to_live(monkeypatch):
    repo, service, case, supervisor = _service_with_draft()
    monkeypatch.setenv("EMAIL_SEND_MODE", "simulate")
    monkeypatch.setattr(GmailConnector, "send", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("network provider called")))
    simulated = service.approve_draft(case.id, DraftDecision(draft_id="draft_gmail"), supervisor)
    assert simulated.drafts[0].status == DraftStatus.SIMULATED

    _gmail_env(monkeypatch, send=True)
    calls: list[bool] = []
    monkeypatch.setattr(GmailConnector, "send", lambda *_a, **_k: calls.append(True) or {"status": 200, "id": "sent"})
    sent = service.approve_draft(case.id, DraftDecision(draft_id="draft_gmail"), supervisor)
    assert sent.drafts[0].status == DraftStatus.SENT
    assert sent.drafts[0].delivery and sent.drafts[0].delivery.mode == "live"
    assert len(calls) == 1
    service.approve_draft(case.id, DraftDecision(draft_id="draft_gmail"), supervisor)
    assert len(calls) == 1
    assert repo.get_case(case.id).drafts[0].status == DraftStatus.SENT


def test_unknown_email_provider_modes_fail_configuration(monkeypatch):
    monkeypatch.setenv("EMAIL_PROVIDER", "unexpected")
    with pytest.raises(ConfigurationError, match="none, bundle, gmail"):
        email_connectors.get_connector()
    with pytest.raises(ConfigurationError, match="simulate, gmail"):
        email_connectors.get_outbound_connector("unexpected")


def test_gmail_outbound_success_failure_and_idempotency(monkeypatch):
    _gmail_env(monkeypatch, send=True)
    repo, service, case, supervisor = _service_with_draft()
    calls = []
    monkeypatch.setattr(GmailConnector, "send", lambda *_args, **_kwargs: calls.append(True) or {"status": 200, "id": "sent"})
    sent = service.approve_draft(case.id, DraftDecision(draft_id="draft_gmail"), supervisor)
    assert sent.drafts[0].status == DraftStatus.SENT
    service.approve_draft(case.id, DraftDecision(draft_id="draft_gmail"), supervisor)
    assert len(calls) == 1

    repo2, service2, case2, supervisor2 = _service_with_draft()
    monkeypatch.delenv("GMAIL_CLIENT_ID")
    with pytest.raises(Exception) as caught:
        service2.approve_draft(case2.id, DraftDecision(draft_id="draft_gmail"), supervisor2)
    assert getattr(caught.value, "status_code", None) == 502
    assert repo2.get_case(case2.id).drafts[0].status == DraftStatus.SEND_FAILED
    monkeypatch.setenv("GMAIL_CLIENT_ID", "test-client-id")
    retried = service2.approve_draft(
        case2.id,
        DraftDecision(draft_id="draft_gmail"),
        supervisor2,
    )
    assert retried.drafts[0].status == DraftStatus.SENT


def test_gmail_share_confirmation_preserves_id_and_custom_message(monkeypatch):
    _gmail_env(monkeypatch, send=True)
    monkeypatch.setattr(GmailConnector, "send", lambda *_args, **_kwargs: {"status": 200, "id": "sent"})
    repo, service, case, supervisor = _service_with_draft()
    custom = "Exact custom Gmail share message"
    preview = service.share(case.id, ShareRequest(
        recipient_type=RecipientType.NOTIFY_PARTY_CONTACT,
        recipient_party_id="p_safqa",
        message=custom,
        preview_only=True,
    ), supervisor)
    original = copy.deepcopy(preview["share"])
    sent = service.confirm_share(case.id, original["id"], supervisor)
    assert sent["share"]["id"] == original["id"]
    assert sent["share"]["status"] == "SENT"
    assert sent["share"]["message"] == original["message"]
    assert sent["share"]["message"].startswith(custom)
    assert len(repo.list_shares(case.id)) == 1


def test_gmail_share_retry_after_database_failure_does_not_resend(monkeypatch):
    class FailOnceFinalizeRepository(MemoryRepository):
        def __init__(self):
            super().__init__()
            self.fail_once = True

        def complete_share_confirmation(self, *args, **kwargs):
            if self.fail_once:
                self.fail_once = False
                raise RuntimeError("transient database failure")
            return super().complete_share_confirmation(*args, **kwargs)

    _gmail_env(monkeypatch, send=True)
    calls = []
    monkeypatch.setattr(
        GmailConnector,
        "send",
        lambda *_args, **_kwargs: calls.append(True) or {"status": 200, "id": "gmail-once"},
    )
    repo = FailOnceFinalizeRepository()
    repo.load_file(ROOT / "supabase" / "seed" / "snapshot.json")
    service = CaseService(repo)
    supervisor = repo.get_user("u_sup_1")
    preview = service.share(
        "case_email_004",
        ShareRequest(
            recipient_type=RecipientType.NOTIFY_PARTY_CONTACT,
            recipient_party_id="p_safqa",
            preview_only=True,
        ),
        supervisor,
    )
    share_id = preview["share"]["id"]

    with pytest.raises(RuntimeError, match="transient database failure"):
        service.confirm_share("case_email_004", share_id, supervisor)

    accepted = repo.get_share(share_id)
    assert accepted is not None and accepted.status == "DELIVERY_ACCEPTED"
    assert accepted.delivery_provider == "gmail"
    assert accepted.provider_message_id == "gmail-once"
    repo.parties["p_safqa"].approved = False

    completed = service.confirm_share("case_email_004", share_id, supervisor)

    assert completed["share"]["status"] == "SENT"
    assert len(calls) == 1
    assert sum(
        event.action == "NOTIFY_PARTY_SENT"
        and event.after
        and event.after.get("provider_message_id") == "gmail-once"
        for event in repo.list_audit("case_email_004")
    ) == 1


def test_gmail_draft_ambiguous_timeout_blocks_automatic_resend(monkeypatch):
    _gmail_env(monkeypatch, send=True)
    calls = []

    def accepted_then_timed_out(*_args, **_kwargs):
        calls.append("provider-accepted")
        raise TimeoutError("response lost after acceptance")

    monkeypatch.setattr(GmailConnector, "send", accepted_then_timed_out)
    repo, service, case, supervisor = _service_with_draft()

    with pytest.raises(Exception) as first:
        service.approve_draft(
            case.id,
            DraftDecision(draft_id="draft_gmail"),
            supervisor,
        )

    assert getattr(first.value, "status_code", None) == 502
    assert getattr(first.value, "detail", {}).get("retryable") is False
    persisted = repo.get_case(case.id)
    assert persisted.drafts[0].status == DraftStatus.DELIVERY_UNKNOWN
    assert any(
        error.category.value == "NOTIFICATION_ERROR" and not error.retryable
        for error in persisted.errors
    )
    assert any(
        event.action == "NOTIFICATION_OUTCOME_UNKNOWN"
        for event in repo.list_audit(case.id)
    )

    with pytest.raises(Exception) as repeated:
        service.approve_draft(
            case.id,
            DraftDecision(draft_id="draft_gmail"),
            supervisor,
        )

    assert getattr(repeated.value, "status_code", None) == 409
    assert getattr(repeated.value, "detail", {}).get("retryable") is False
    assert calls == ["provider-accepted"]


def test_gmail_share_ambiguous_timeout_blocks_automatic_resend(monkeypatch):
    _gmail_env(monkeypatch, send=True)
    calls = []

    def accepted_then_timed_out(*_args, **_kwargs):
        calls.append("provider-accepted")
        raise TimeoutError("response lost after acceptance")

    monkeypatch.setattr(GmailConnector, "send", accepted_then_timed_out)
    repo = MemoryRepository()
    repo.load_file(ROOT / "supabase" / "seed" / "snapshot.json")
    service = CaseService(repo)
    supervisor = repo.get_user("u_sup_1")
    preview = service.share(
        "case_email_004",
        ShareRequest(
            recipient_type=RecipientType.NOTIFY_PARTY_CONTACT,
            recipient_party_id="p_safqa",
            preview_only=True,
        ),
        supervisor,
    )
    share_id = preview["share"]["id"]

    with pytest.raises(Exception) as first:
        service.confirm_share("case_email_004", share_id, supervisor)

    assert getattr(first.value, "status_code", None) == 502
    assert getattr(first.value, "detail", {}).get("retryable") is False
    persisted = repo.get_share(share_id)
    assert persisted is not None and persisted.status == "DELIVERY_UNKNOWN"
    assert "p_safqa" not in repo.get_case("case_email_004").shared_with
    assert not any(
        event.action == "NOTIFY_PARTY_SENT"
        and event.after
        and event.after.get("share_id") == share_id
        for event in repo.list_audit("case_email_004")
    )

    with pytest.raises(Exception) as repeated:
        service.confirm_share("case_email_004", share_id, supervisor)

    assert getattr(repeated.value, "status_code", None) == 409
    assert getattr(repeated.value, "detail", {}).get("retryable") is False
    assert calls == ["provider-accepted"]


def test_gmail_share_definite_preflight_failure_can_retry(monkeypatch):
    _gmail_env(monkeypatch, send=True)
    calls = []
    monkeypatch.setattr(
        GmailConnector,
        "send",
        lambda *_args, **_kwargs: calls.append(True)
        or {"status": 200, "id": "gmail-after-preflight-fix"},
    )
    repo = MemoryRepository()
    repo.load_file(ROOT / "supabase" / "seed" / "snapshot.json")
    service = CaseService(repo)
    supervisor = repo.get_user("u_sup_1")
    preview = service.share(
        "case_email_004",
        ShareRequest(
            recipient_type=RecipientType.NOTIFY_PARTY_CONTACT,
            recipient_party_id="p_safqa",
            preview_only=True,
        ),
        supervisor,
    )
    share_id = preview["share"]["id"]
    monkeypatch.delenv("GMAIL_CLIENT_ID")

    with pytest.raises(Exception) as first:
        service.confirm_share("case_email_004", share_id, supervisor)

    assert getattr(first.value, "status_code", None) == 502
    assert getattr(first.value, "detail", {}).get("retryable") is True
    failed = repo.get_share(share_id)
    assert failed is not None and failed.status == "DELIVERY_FAILED"
    assert calls == []

    monkeypatch.setenv("GMAIL_CLIENT_ID", "test-client-id")
    completed = service.confirm_share("case_email_004", share_id, supervisor)

    assert completed["share"]["status"] == "SENT"
    assert completed["share"]["provider_message_id"] == "gmail-after-preflight-fix"
    assert calls == [True]


def test_gmail_share_inflight_delivery_blocks_automatic_resend(monkeypatch):
    _gmail_env(monkeypatch, send=True)
    calls = []
    monkeypatch.setattr(
        GmailConnector,
        "send",
        lambda *_args, **_kwargs: calls.append(True) or {"status": 200, "id": "unexpected"},
    )
    repo = MemoryRepository()
    repo.load_file(ROOT / "supabase" / "seed" / "snapshot.json")
    service = CaseService(repo)
    supervisor = repo.get_user("u_sup_1")
    preview = service.share(
        "case_email_004",
        ShareRequest(
            recipient_type=RecipientType.NOTIFY_PARTY_CONTACT,
            recipient_party_id="p_safqa",
            preview_only=True,
        ),
        supervisor,
    )
    share = repo.get_share(preview["share"]["id"])
    assert share is not None
    share.status = "DELIVERING"
    share.delivery_provider = "gmail"
    repo.save_share(share)

    with pytest.raises(Exception) as caught:
        service.confirm_share("case_email_004", share.id, supervisor)

    assert getattr(caught.value, "status_code", None) == 409
    assert getattr(caught.value, "detail", {}).get("retryable") is False
    assert calls == []


def test_concurrent_gmail_share_confirmation_sends_once(monkeypatch):
    class DetachedShareRepository(MemoryRepository):
        def __init__(self):
            super().__init__()
            self.confirm_barrier: threading.Barrier | None = None
            self.confirm_reads = 0

        def get_share(self, share_id):
            with self._lock:
                share = self.shares.get(share_id)
                detached = share.model_copy(deep=True) if share else None
                barrier = self.confirm_barrier
                if barrier is not None:
                    self.confirm_reads += 1
                    if self.confirm_reads == barrier.parties:
                        self.confirm_barrier = None
            if barrier is not None:
                barrier.wait()
            return detached

    _gmail_env(monkeypatch, send=True)
    calls = []
    monkeypatch.setattr(
        GmailConnector,
        "send",
        lambda *_args, **_kwargs: calls.append(True) or {"status": 200, "id": "gmail-concurrent"},
    )
    repo = DetachedShareRepository()
    repo.load_file(ROOT / "supabase" / "seed" / "snapshot.json")
    service = CaseService(repo)
    supervisor = repo.get_user("u_sup_1")
    preview = service.share(
        "case_email_004",
        ShareRequest(
            recipient_type=RecipientType.NOTIFY_PARTY_CONTACT,
            recipient_party_id="p_safqa",
            preview_only=True,
        ),
        supervisor,
    )
    share_id = preview["share"]["id"]
    repo.confirm_barrier = threading.Barrier(2)

    def confirm_once():
        try:
            return service.confirm_share("case_email_004", share_id, supervisor)
        except Exception as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: confirm_once(), range(2)))

    persisted = repo.get_share(share_id)
    assert persisted is not None and persisted.status == "SENT"
    assert persisted.provider_message_id == "gmail-concurrent"
    assert len(calls) == 1
    assert any(isinstance(result, dict) for result in results)
    assert all(
        isinstance(result, dict) or getattr(result, "status_code", None) == 409
        for result in results
    )
