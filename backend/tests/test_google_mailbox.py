"""Sign in with Google + per-user mailboxes: state, callback, tagging, polling, outbound from the owner, scheduler."""
from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("REPO_BACKEND", "memory")
os.environ["AUTO_SEED"] = "0"
os.environ["LLM_PROVIDER"] = "none"

import app.api.google_auth_routes as google_auth  # noqa: E402
from app.auth.mailbox_tokens import MailboxTokenError, decrypt_token, encrypt_token  # noqa: E402
from app.config import get_repo  # noqa: E402
from app.connectors.email_connectors import GmailConnector, InboundMessage  # noqa: E402
from app.contracts.schemas import DraftDecision, DraftStatus, UserMailbox  # noqa: E402
from app.main import app  # noqa: E402
from app.repositories.memory import MemoryRepository  # noqa: E402
from app.services import mailbox_poller  # noqa: E402
from app.services.case_service import CaseService  # noqa: E402
from app.services.mailbox_service import MailboxPollError, poll_connector, poll_user_mailbox  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
client = TestClient(app, follow_redirects=False)
SEND = "https://www.googleapis.com/auth/gmail.send"
READ = "https://www.googleapis.com/auth/gmail.readonly"


def _google_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "web-client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "web-client-secret")
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    monkeypatch.setenv("REGISTER_ALLOWED_ROLES", "ADMIN,SUPERVISOR,OPERATIONS_STAFF")


def _fake_google(monkeypatch: pytest.MonkeyPatch, *, email="new.user@gmail.com", verified=True, refresh="rt-secret", scopes=(READ, SEND), name="New User"):
    monkeypatch.setattr(google_auth, "_exchange_code", lambda code: {"access_token": f"at-{code}", "refresh_token": refresh, "scope": " ".join(scopes), "id_token": "x"})
    monkeypatch.setattr(google_auth, "_fetch_userinfo", lambda token: {"sub": "google-sub-1", "email": email, "email_verified": verified, "name": name})


def _fragment(location: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(location).fragment).items()}


def _mailbox(user_id="u_ops_1", address="najiha.box@gmail.com", scopes=(READ, SEND), status="active") -> UserMailbox:
    return UserMailbox(user_id=user_id, address=address, refresh_token_enc=encrypt_token("rt-" + user_id), scopes=list(scopes), status=status, connected_at=datetime.utcnow())


class _Conn:
    """Connector double yielding canned messages (or raising)."""
    name = "gmail"

    def __init__(self, messages=None, fail: Exception | None = None, address="box@example.com"):
        self.messages = messages or []
        self.fail = fail
        self.address = address
        self.sent: list[dict] = []

    def fetch(self, since=None, limit=50):
        if self.fail:
            raise self.fail
        yield from self.messages[:limit]

    def send(self, to, subject, body, cc=None):
        self.sent.append({"to": to, "subject": subject})
        return {"status": 200, "id": f"sent-{len(self.sent)}"}


def _inbound(email_id: str, sender="customer@example.com") -> InboundMessage:
    return InboundMessage(raw={"email_id": email_id, "from": sender, "to": ["box@example.com"], "subject": f"REQUEST BL DRAFT {email_id}",
                               "body": "Attached are the SI and draft BL. Please check the details and confirm.", "attachments": []}, provider="gmail")


# ---------------------------------------------------------------- token encryption + state
def test_refresh_tokens_round_trip_and_reject_wrong_key(monkeypatch):
    enc = encrypt_token("1//refresh")
    assert enc != "1//refresh" and decrypt_token(enc) == "1//refresh"
    monkeypatch.setenv("MAILBOX_TOKEN_KEY", "Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4YWJjZA==")
    with pytest.raises(MailboxTokenError):
        decrypt_token(enc)


def test_state_is_signed_single_use_and_expires(monkeypatch):
    state = google_auth.make_state({"role": "ADMIN", "next": "/welcome", "connect": None})
    assert google_auth.read_state(state)["role"] == "ADMIN"
    assert google_auth.read_state(state) is None, "nonce must be single-use"
    assert google_auth.read_state(state[:-2] + "zz") is None, "tampered signature"
    monkeypatch.setattr(google_auth, "STATE_TTL_S", -1)
    assert google_auth.read_state(google_auth.make_state({"role": None})) is None


# ---------------------------------------------------------------- start
def test_start_builds_consent_url_with_offline_gmail_scopes(monkeypatch):
    _google_env(monkeypatch)
    r = client.get("/auth/google/start", params={"role": "ADMIN", "next": "/"})
    assert r.status_code == 200, r.text
    url = r.json()["url"]
    q = parse_qs(urlparse(url).query)
    assert url.startswith(google_auth.AUTH_URI) and q["client_id"] == ["web-client-id"] and q["access_type"] == ["offline"] and q["prompt"] == ["consent"]
    assert set(q["scope"][0].split()) >= {"openid", "email", READ, SEND}
    assert google_auth.read_state(q["state"][0])["role"] == "ADMIN"
    assert client.get("/auth/config").json()["google_enabled"] is True


def test_start_rejects_role_outside_allowlist_and_jwt_mode(monkeypatch):
    _google_env(monkeypatch)
    monkeypatch.setenv("REGISTER_ALLOWED_ROLES", "OPERATIONS_STAFF")
    assert client.get("/auth/google/start", params={"role": "ADMIN"}).status_code == 403
    monkeypatch.setenv("AUTH_MODE", "jwt")
    assert client.get("/auth/google/start").status_code == 403
    assert client.get("/auth/google/callback", params={"code": "c", "state": "s"}).headers["location"].endswith("error=google_disabled")


def test_start_without_client_config_is_503(monkeypatch):
    for name in ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    assert client.get("/auth/google/start").status_code == 503
    assert client.get("/auth/config").json()["google_enabled"] is False


# ---------------------------------------------------------------- callback
def test_callback_creates_account_connects_mailbox_and_issues_session(monkeypatch):
    _google_env(monkeypatch)
    _fake_google(monkeypatch, email="fresh.signup@gmail.com")
    state = google_auth.make_state({"role": "ADMIN", "next": "/welcome", "connect": None})
    r = client.get("/auth/google/callback", params={"code": "code-1", "state": state})
    assert r.status_code == 302
    frag = _fragment(r.headers["location"])
    assert r.headers["location"].startswith("http://localhost:3000/auth/callback#")
    assert frag["token"].startswith("nsa.") and frag["new"] == "1" and frag["next"] == "/welcome" and frag["mailbox"] == "fresh.signup@gmail.com"

    repo = get_repo()
    user = repo.get_user_by_email("fresh.signup@gmail.com")
    assert user and [r.value for r in user.roles] == ["ADMIN"] and user.display_name == "New User"
    mailbox = repo.get_mailbox(user.id)
    assert mailbox and mailbox.address == "fresh.signup@gmail.com" and mailbox.can_send()
    assert mailbox.refresh_token_enc != "rt-secret" and decrypt_token(mailbox.refresh_token_enc) == "rt-secret"

    me = client.get("/me", headers={"Authorization": f"Bearer {frag['token']}"}).json()
    assert me["mailbox"]["connected"] is True and me["mailbox"]["address"] == "fresh.signup@gmail.com" and "refresh_token" not in str(me["mailbox"])
    actions = [e["action"] for e in client.get("/audit", headers={"X-User-Id": "u_admin_1"}).json()["events"] if e["actor_id"] == user.id]
    assert {"REGISTER", "MAILBOX_CONNECTED", "LOGIN"} <= set(actions)
    # password login is not available for a Google-only account
    assert client.post("/auth/login", json={"email": "fresh.signup@gmail.com", "password": "anything123"}).status_code == 401


def test_callback_existing_email_logs_in_without_new_account(monkeypatch):
    _google_env(monkeypatch)
    _fake_google(monkeypatch, email="hanna_azhari@aprilasia.com", name="Ignored Name")
    before = len(get_repo().list_users())
    r = client.get("/auth/google/callback", params={"code": "code-2", "state": google_auth.make_state({"role": "ADMIN", "next": "/", "connect": None})})
    frag = _fragment(r.headers["location"])
    assert frag["new"] == "0" and len(get_repo().list_users()) == before
    me = client.get("/me", headers={"Authorization": f"Bearer {frag['token']}"}).json()
    assert me["id"] == "u_ops_1" and me["roles"] == ["OPERATIONS_STAFF"], "existing role is kept; state role is ignored"
    assert get_repo().get_mailbox("u_ops_1").address == "hanna_azhari@aprilasia.com"


def test_callback_connect_mode_links_mailbox_to_current_account(monkeypatch):
    _google_env(monkeypatch)
    token = client.post("/auth/login", json={"email": "hari_mardianto@aprilasia.com", "password": "novaship123"}).json()["token"]
    start = client.get("/auth/google/start", headers={"Authorization": f"Bearer {token}"}).json()
    assert start["connect"] is True
    state = parse_qs(urlparse(start["url"]).query)["state"][0]
    _fake_google(monkeypatch, email="hari.personal@gmail.com")
    r = client.get("/auth/google/callback", params={"code": "code-3", "state": state})
    assert _fragment(r.headers["location"])["mailbox"] == "hari.personal@gmail.com"
    assert get_repo().get_mailbox("u_sup_1").address == "hari.personal@gmail.com"
    assert get_repo().get_user_by_email("hari.personal@gmail.com") is None, "connect must not create a second account"


@pytest.mark.parametrize("setup, expected", [
    (dict(refresh=""), "no_refresh_token"),
    (dict(verified=False), "email_unverified"),
    (dict(email="nobody.new@gmail.com", role="AUDITOR"), "role_not_allowed"),
])
def test_callback_failures_redirect_to_login_with_reason(monkeypatch, setup, expected):
    _google_env(monkeypatch)
    role = setup.pop("role", "OPERATIONS_STAFF")
    _fake_google(monkeypatch, **setup)
    r = client.get("/auth/google/callback", params={"code": "c", "state": google_auth.make_state({"role": role, "next": "/", "connect": None})})
    assert r.status_code == 302 and r.headers["location"] == f"http://localhost:3000/login?error={expected}"
    assert client.get("/auth/google/callback", params={"code": "c", "state": "garbage"}).headers["location"].endswith("error=bad_state")
    assert client.get("/auth/google/callback", params={"error": "access_denied"}).headers["location"].endswith("error=google_denied")


def test_callback_registration_disabled_in_local_mode_still_allows_existing_users(monkeypatch):
    _google_env(monkeypatch)
    monkeypatch.setenv("AUTH_MODE", "local")
    monkeypatch.setenv("SESSION_SECRET", "a-sufficiently-long-non-placeholder-secret-value")
    monkeypatch.setenv("SELF_REGISTRATION_ENABLED", "0")
    _fake_google(monkeypatch, email="stranger@gmail.com")
    r = client.get("/auth/google/callback", params={"code": "c", "state": google_auth.make_state({"role": None, "next": "/", "connect": None})})
    assert r.headers["location"].endswith("error=registration_disabled")
    _fake_google(monkeypatch, email="hanna_azhari@aprilasia.com")
    r = client.get("/auth/google/callback", params={"code": "c", "state": google_auth.make_state({"role": None, "next": "/", "connect": None})})
    assert "/auth/callback#" in r.headers["location"]


# ---------------------------------------------------------------- polling + tagging
def test_poll_user_mailbox_tags_cases_and_updates_mailbox_state(monkeypatch):
    repo = MemoryRepository()
    service = CaseService(repo)
    mailbox = _mailbox()
    repo.save_mailbox(mailbox)
    conn = _Conn([_inbound("gm-1"), _inbound("gm-2")])
    result = poll_connector(service, conn, actor_id="u_ops_1", limit=10, mailbox=mailbox)
    assert len(result["created"]) == 2 and result["mailbox"] == "najiha.box@gmail.com"
    for case in repo.list_cases():
        email = repo.get_email(case.source_email_id)
        assert email.mailbox_user_id == "u_ops_1" and email.mailbox_address == "najiha.box@gmail.com"
    assert repo.get_mailbox("u_ops_1").last_polled_at is not None
    again = poll_connector(service, conn, actor_id="u_ops_1", limit=10, mailbox=mailbox)
    assert again["created"] == [] and again["duplicates_skipped"] == 2

    failing = _Conn(fail=RuntimeError("boom"))
    with pytest.raises(MailboxPollError):
        poll_connector(service, failing, actor_id="u_ops_1", mailbox=mailbox)
    assert repo.get_mailbox("u_ops_1").status == "error" and repo.get_mailbox("u_ops_1").last_error == "RuntimeError"
    poll_connector(service, _Conn(), actor_id="u_ops_1", mailbox=mailbox)
    assert repo.get_mailbox("u_ops_1").status == "active" and repo.get_mailbox("u_ops_1").last_error is None


def test_poll_route_prefers_connected_mailbox_and_lists_by_mailbox(monkeypatch):
    _google_env(monkeypatch)
    repo = get_repo()
    repo.save_mailbox(_mailbox(user_id="u_ops_2", address="deswita.box@gmail.com"))
    conn = _Conn([_inbound("route-1")], address="deswita.box@gmail.com")
    monkeypatch.setattr(GmailConnector, "from_mailbox", classmethod(lambda cls, mb: conn))
    r = client.post("/connectors/poll", params={"limit": 5}, headers={"X-User-Id": "u_ops_2"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mailbox"] == "deswita.box@gmail.com" and len(body["created"]) == 1
    case_id = body["created"][0]

    mine = client.get("/cases", params={"mailbox": "me"}, headers={"X-User-Id": "u_ops_2"}).json()
    assert [row["id"] for row in mine["items"]] == [case_id] and mine["items"][0]["mailbox"] == "deswita.box@gmail.com"
    assert all(row["id"] != case_id for row in client.get("/cases", params={"mailbox": "shared"}, headers={"X-User-Id": "u_ops_2"}).json()["items"])
    assert client.post("/connectors/poll", params={"source": "mine"}, headers={"X-User-Id": "u_ops_3"}).status_code == 400
    monkeypatch.setenv("EMAIL_PROVIDER", "none")
    assert client.post("/connectors/poll", params={"source": "shared"}, headers={"X-User-Id": "u_ops_2"}).status_code == 400
    monkeypatch.delenv("EMAIL_PROVIDER", raising=False)

    r = client.delete("/me/mailbox", headers={"X-User-Id": "u_ops_2"})
    assert r.status_code == 200 and repo.get_mailbox("u_ops_2") is None
    assert client.get("/me/mailbox", headers={"X-User-Id": "u_ops_2"}).json()["connected"] is False
    assert client.delete("/me/mailbox", headers={"X-User-Id": "u_ops_2"}).status_code == 404


# ---------------------------------------------------------------- outbound from the owner's mailbox
def _tagged_case(repo: MemoryRepository, mailbox: UserMailbox | None):
    repo.load_file(ROOT / "supabase" / "seed" / "snapshot.json")
    case = next(c.model_copy(deep=True) for c in repo.list_cases() if c.drafts and c.drafts[0].requires_external_approval)
    email = repo.get_email(case.source_email_id).model_copy(deep=True)
    email.id, case.id = f"{email.id}_owned", f"{case.id}_owned"
    case.source_email_id = email.id
    case.drafts[0].id, case.drafts[0].to, case.drafts[0].status = "draft_owned", ["recipient@example.com"], DraftStatus.PROPOSED
    if mailbox:
        email.mailbox_user_id, email.mailbox_address = mailbox.user_id, mailbox.address
        repo.save_mailbox(mailbox)
    repo.save_email(email)
    repo.save_case(case)
    return case


def test_approved_draft_sends_from_owner_mailbox_and_audits_from(monkeypatch):
    monkeypatch.setenv("EMAIL_SEND_MODE", "gmail")
    repo = MemoryRepository()
    mailbox = _mailbox(user_id="u_ops_1", address="owner@gmail.com")
    case = _tagged_case(repo, mailbox)
    owner_conn, shared_calls = _Conn(address="owner@gmail.com"), []
    monkeypatch.setattr(GmailConnector, "from_mailbox", classmethod(lambda cls, mb: owner_conn))
    monkeypatch.setattr("app.connectors.email_connectors.get_outbound_connector", lambda mode=None: shared_calls.append(mode) or _Conn(address="shared@example.com"))
    result = CaseService(repo).approve_draft(case.id, DraftDecision(draft_id="draft_owned"), repo.get_user("u_sup_1"))
    assert result.drafts[0].status == DraftStatus.SENT and owner_conn.sent and not shared_calls
    sent = [e for e in repo.list_audit(case.id) if e.action == "NOTIFICATION_SENT"]
    assert sent and sent[-1].after["from"] == "owner@gmail.com"


def test_outbound_falls_back_to_shared_mailbox_without_send_scope(monkeypatch):
    monkeypatch.setenv("EMAIL_SEND_MODE", "gmail")
    repo = MemoryRepository()
    case = _tagged_case(repo, _mailbox(user_id="u_ops_1", address="readonly@gmail.com", scopes=(READ,)))
    shared = _Conn(address="shared@example.com")
    monkeypatch.setattr(GmailConnector, "from_mailbox", classmethod(lambda cls, mb: (_ for _ in ()).throw(AssertionError("owner mailbox must not be used"))))
    monkeypatch.setattr("app.connectors.email_connectors.get_outbound_connector", lambda mode=None: shared)
    CaseService(repo).approve_draft(case.id, DraftDecision(draft_id="draft_owned"), repo.get_user("u_sup_1"))
    assert shared.sent and [e for e in repo.list_audit(case.id) if e.action == "NOTIFICATION_SENT"][-1].after["from"] == "shared@example.com"


def test_simulate_mode_never_touches_owner_mailbox(monkeypatch):
    monkeypatch.setenv("EMAIL_SEND_MODE", "simulate")
    repo = MemoryRepository()
    case = _tagged_case(repo, _mailbox(user_id="u_ops_1", address="owner@gmail.com"))
    monkeypatch.setattr(GmailConnector, "from_mailbox", classmethod(lambda cls, mb: (_ for _ in ()).throw(AssertionError("network provider called"))))
    result = CaseService(repo).approve_draft(case.id, DraftDecision(draft_id="draft_owned"), repo.get_user("u_sup_1"))
    assert result.drafts[0].status == DraftStatus.SIMULATED


# ---------------------------------------------------------------- scheduler
def test_poll_all_mailboxes_isolates_failures_and_never_raises(monkeypatch):
    repo = get_repo()
    for mb in repo.list_mailboxes():
        repo.delete_mailbox(mb.user_id)
    repo.save_mailbox(_mailbox(user_id="u_ops_3", address="good@gmail.com"))
    repo.save_mailbox(_mailbox(user_id="u_ops_4", address="bad@gmail.com"))
    repo.save_mailbox(_mailbox(user_id="u_audit_1", address="gone@gmail.com", status="revoked"))
    conns = {"good@gmail.com": _Conn([_inbound("sched-1", sender="a@example.com")]), "bad@gmail.com": _Conn(fail=RuntimeError("expired"))}
    monkeypatch.setattr(GmailConnector, "from_mailbox", classmethod(lambda cls, mb: conns[mb.address]))
    monkeypatch.setenv("EMAIL_PROVIDER", "none")
    summary = mailbox_poller.poll_all_mailboxes()
    assert summary["mailboxes"]["good@gmail.com"]["created"] == 1
    assert summary["mailboxes"]["bad@gmail.com"] == {"error": "RuntimeError"} and "gone@gmail.com" not in summary["mailboxes"]
    assert repo.get_mailbox("u_ops_4").status == "error"
    assert summary["shared"] is None
    for uid in ("u_ops_3", "u_ops_4", "u_audit_1"):
        repo.delete_mailbox(uid)


def test_background_poller_is_off_by_default_and_starts_when_configured(monkeypatch):
    monkeypatch.setenv("GMAIL_POLL_INTERVAL_SECONDS", "0")
    assert mailbox_poller.start_background_poller() is False
    monkeypatch.setenv("GMAIL_POLL_INTERVAL_SECONDS", "3600")
    assert mailbox_poller.poll_interval_seconds() == 3600
    assert mailbox_poller.start_background_poller() is True
    assert mailbox_poller.start_background_poller() is False, "second start is a no-op while the thread lives"
    mailbox_poller.stop_background_poller()
    time.sleep(0.05)


def test_memory_snapshot_round_trips_mailboxes():
    repo = MemoryRepository()
    repo.save_mailbox(_mailbox(user_id="u_ops_1"))
    clone = MemoryRepository()
    clone.load(repo.dump())
    assert clone.get_mailbox("u_ops_1").address == "najiha.box@gmail.com" and decrypt_token(clone.get_mailbox("u_ops_1").refresh_token_enc) == "rt-u_ops_1"


def test_individual_mailboxes_only_no_shared_mailbox_configured(monkeypatch):
    """EMAIL_PROVIDER=none and no GMAIL_*: fetch and send work through the user's own Gmail; other cases fail clearly, not with a 500."""
    for name in ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN", "GMAIL_ADDRESS", "MICROSOFT_CLIENT_ID", "MICROSOFT_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("EMAIL_PROVIDER", "none")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "web-client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "web-client-secret")
    repo = get_repo()
    repo.save_mailbox(_mailbox(user_id="u_ops_4", address="solo@gmail.com"))
    conn = _Conn([_inbound("solo-1")], address="solo@gmail.com")
    monkeypatch.setattr(GmailConnector, "from_mailbox", classmethod(lambda cls, mb: conn))
    fetched = client.post("/connectors/poll?limit=5", headers={"X-User-Id": "u_ops_4"}).json()
    assert fetched["mailbox"] == "solo@gmail.com" and len(fetched["created"]) == 1
    assert client.post("/connectors/poll?source=shared", headers={"X-User-Id": "u_ops_4"}).status_code == 400

    monkeypatch.setenv("EMAIL_SEND_MODE", "gmail")
    service = CaseService(repo)
    case = repo.get_case(fetched["created"][0])
    assert service._outbound_mailbox(case).address == "solo@gmail.com", "reply would leave from the user's own Gmail"
    # a case that did not arrive through any connected mailbox (plain webhook) cannot be answered: clear 400, draft left retryable
    plain = client.post("/webhooks/email", json={"email_id": "solo-plain-1", "from": "customer@example.com", "subject": "REQUEST BL DRAFT solo-plain",
                                                 "body": "Attached are the SI and draft BL. Please check the details and confirm.", "attachments": []}, headers={"X-User-Id": "u_sup_1"}).json()
    seeded = repo.get_case(plain["id"])
    assert seeded.drafts, "a missing-document request draft is generated"
    r = client.post(f"/cases/{seeded.id}/approve", json={"draft_id": seeded.drafts[0].id}, headers={"X-User-Id": "u_sup_1"})
    assert r.status_code == 502 and "no mailbox can send" in r.json()["detail"]["error"] and r.json()["detail"]["retryable"] is True
    failed = repo.get_case(seeded.id)
    assert failed.drafts[0].status == DraftStatus.SEND_FAILED
    assert failed.errors and failed.errors[-1].message.startswith("No mailbox can send this reply") and "Connect your mailbox" in failed.errors[-1].recovery
    cfg = client.get("/auth/config").json()
    assert cfg["shared_mailbox_configured"] is False and cfg["google_enabled"] is True and cfg["microsoft_enabled"] is False
    assert client.get("/me", headers={"X-User-Id": "u_ops_4"}).json()["mailbox"]["providers"]["shared_mailbox_configured"] is False
    repo.delete_mailbox("u_ops_4")
    assert client.post("/connectors/poll", headers={"X-User-Id": "u_ops_4"}).json()["detail"]["code"] == "NO_MAILBOX_CONNECTED"


def test_new_mail_notifications_list_only_the_callers_mailbox_cases(monkeypatch):
    """The bell's 'New mail' section: cases that arrived through the caller's connected mailbox, newest first; nobody else's."""
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "web-client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "web-client-secret")
    repo = get_repo()
    repo.save_mailbox(_mailbox(user_id="u_ops_3", address="bell.box@gmail.com"))
    conn = _Conn([_inbound("bell-1"), _inbound("bell-2")], address="bell.box@gmail.com")
    monkeypatch.setattr(GmailConnector, "from_mailbox", classmethod(lambda cls, mb: conn))
    created = client.post("/connectors/poll?limit=5", headers={"X-User-Id": "u_ops_3"}).json()["created"]
    assert len(created) == 2
    feed = client.get("/me/notifications", headers={"X-User-Id": "u_ops_3"}).json()
    mine = [m for m in feed["new_mail"] if m["case_id"] in created]   # earlier tests also fetched into this user's mailbox
    assert len(mine) == 2 and feed["new_mail"].index(mine[0]) < feed["new_mail"].index(mine[1]) and mine[0]["case_id"] == created[-1], "newest first"
    assert all(m["kind"] == "new_mail" and m["mailbox"] == "bell.box@gmail.com" and m["sender"] == "customer@example.com" for m in mine)
    assert feed["new_mail_total"] == len(feed["new_mail"]) and mine[0]["subject"].startswith("REQUEST BL DRAFT")
    other = client.get("/me/notifications", headers={"X-User-Id": "u_admin_1"}).json()
    assert not {m["case_id"] for m in other["new_mail"]} & set(created), "another user's mailbox never appears as my new mail"
    repo.delete_mailbox("u_ops_3")
