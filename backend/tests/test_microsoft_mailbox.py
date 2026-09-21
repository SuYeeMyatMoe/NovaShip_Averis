"""Sign in with Microsoft + Connect Outlook: two-step consent, Graph connector, token rotation, fetch/reply through the user's Outlook."""
from __future__ import annotations

import base64
import json
import os
from datetime import datetime
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("REPO_BACKEND", "memory")
os.environ["AUTO_SEED"] = "0"
os.environ["LLM_PROVIDER"] = "none"

import app.api.microsoft_auth_routes as ms_auth  # noqa: E402
import app.connectors.email_connectors as connectors  # noqa: E402
from app.auth.mailbox_tokens import decrypt_token, encrypt_token  # noqa: E402
from app.config import get_repo  # noqa: E402
from app.connectors.email_connectors import MicrosoftGraphConnector, connector_for_mailbox  # noqa: E402
from app.contracts.schemas import DraftDecision, DraftStatus, UserMailbox  # noqa: E402
from app.main import app  # noqa: E402
from app.repositories.memory import MemoryRepository  # noqa: E402
from app.services.case_service import CaseService  # noqa: E402
from app.services.mailbox_service import poll_user_mailbox  # noqa: E402

client = TestClient(app, follow_redirects=False)


def _ms_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MICROSOFT_CLIENT_ID", "entra-app-id")
    monkeypatch.setenv("MICROSOFT_CLIENT_SECRET", "entra-secret")
    monkeypatch.setenv("MICROSOFT_TENANT", "common")
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    monkeypatch.setenv("REGISTER_ALLOWED_ROLES", "ADMIN,SUPERVISOR,OPERATIONS_STAFF")


def _fake_ms(monkeypatch, *, email="ops@contoso.com", name="Ops Person", scopes="openid profile email offline_access User.Read", refresh="ms-rt-1"):
    monkeypatch.setattr(ms_auth, "_exchange_code", lambda code: {"access_token": f"at-{code}", "refresh_token": refresh, "scope": scopes, "expires_in": 3600})
    monkeypatch.setattr(ms_auth, "_fetch_me", lambda token: {"id": "ms-oid-1", "mail": email, "userPrincipalName": email, "displayName": name})


def _fragment(location: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(location).fragment).items()}


def _login(monkeypatch, email="ops@contoso.com") -> str:
    _fake_ms(monkeypatch, email=email)
    start = client.get("/auth/microsoft/start", params={"intent": "login", "role": "OPERATIONS_STAFF", "next": "/welcome"}).json()
    state = parse_qs(urlparse(start["url"]).query)["state"][0]
    r = client.get("/auth/microsoft/callback", params={"code": "c-login", "state": state})
    assert r.status_code == 302, r.text
    return _fragment(r.headers["location"])["token"]


# ---------------------------------------------------------------- start / login / connect
def test_start_login_asks_identity_only_and_connect_asks_mail(monkeypatch):
    _ms_env(monkeypatch)
    r = client.get("/auth/microsoft/start", params={"intent": "login"})
    assert r.status_code == 200, r.text
    q = parse_qs(urlparse(r.json()["url"]).query)
    assert r.json()["url"].startswith("https://login.microsoftonline.com/common/oauth2/v2.0/authorize")
    assert set(q["scope"][0].split()) == {"openid", "profile", "email", "offline_access", "User.Read"} and q["prompt"] == ["select_account"]
    assert client.get("/auth/microsoft/start", params={"intent": "connect"}).status_code == 401, "connect needs a session"
    assert client.get("/auth/config").json()["microsoft_enabled"] is True
    token = _login(monkeypatch)
    r = client.get("/auth/microsoft/start", params={"intent": "connect", "next": "/"}, headers={"Authorization": f"Bearer {token}"}).json()
    q = parse_qs(urlparse(r["url"]).query)
    assert r["connect"] is True and {"Mail.Read", "Mail.Send", "offline_access"} <= set(q["scope"][0].split()) and q["prompt"] == ["consent"]


def test_login_creates_account_without_mailbox_then_connect_links_outlook(monkeypatch):
    _ms_env(monkeypatch)
    token = _login(monkeypatch, email="new.person@outlook.com")
    me = client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert me["email"] == "new.person@outlook.com" and me["roles"] == ["OPERATIONS_STAFF"] and me["mailbox"]["connected"] is False
    assert client.post("/auth/login", json={"email": "new.person@outlook.com", "password": "whatever123"}).status_code == 401
    actions = [e["action"] for e in client.get("/audit", headers={"X-User-Id": "u_admin_1"}).json()["events"] if e["actor_id"] == me["id"]]
    assert {"REGISTER", "LOGIN"} <= set(actions) and "MAILBOX_CONNECTED" not in actions

    # connect step: mail scopes, refresh token stored encrypted, existing session kept
    start = client.get("/auth/microsoft/start", params={"intent": "connect", "next": "/"}, headers={"Authorization": f"Bearer {token}"}).json()
    state = parse_qs(urlparse(start["url"]).query)["state"][0]
    _fake_ms(monkeypatch, email="new.person@outlook.com", scopes="https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/Mail.Send offline_access User.Read", refresh="ms-rt-mail")
    r = client.get("/auth/microsoft/callback", params={"code": "c-connect", "state": state})
    frag = _fragment(r.headers["location"])
    assert frag["connected"] == "outlook" and frag["mailbox"] == "new.person@outlook.com" and frag["next"] == "/" and "token" not in frag
    mailbox = get_repo().get_mailbox(me["id"])
    assert mailbox.provider == "outlook" and mailbox.can_send() and mailbox.can_read() and decrypt_token(mailbox.refresh_token_enc) == "ms-rt-mail"
    assert client.get("/me/mailbox", headers={"Authorization": f"Bearer {token}"}).json()["provider"] == "outlook"
    gone = client.delete("/me/mailbox", headers={"Authorization": f"Bearer {token}"}).json()
    assert gone["provider"] == "outlook" and gone["google_revoked"] is False and "myaccount.microsoft.com" in gone["note"]


def test_connect_refuses_when_mail_permission_was_not_granted(monkeypatch):
    _ms_env(monkeypatch)
    token = _login(monkeypatch, email="declined@contoso.com")
    start = client.get("/auth/microsoft/start", params={"intent": "connect"}, headers={"Authorization": f"Bearer {token}"}).json()
    state = parse_qs(urlparse(start["url"]).query)["state"][0]
    _fake_ms(monkeypatch, email="declined@contoso.com", scopes="openid User.Read offline_access", refresh="rt")
    r = client.get("/auth/microsoft/callback", params={"code": "c", "state": state})
    assert r.headers["location"].endswith("error=mail_permission_missing")
    assert client.get("/auth/microsoft/callback", params={"error": "access_denied", "error_description": "user said no"}).headers["location"].endswith("error=microsoft_denied")
    # a Microsoft state cannot be replayed against the Google callback
    import app.api.google_auth_routes as google_auth

    state2 = parse_qs(urlparse(client.get("/auth/microsoft/start", params={"intent": "login"}).json()["url"]).query)["state"][0]
    assert client.get("/auth/google/callback", params={"code": "c", "state": state2}).headers["location"].endswith("error=bad_state")


# ---------------------------------------------------------------- Graph connector against a fake transport
class _Graph:
    """Minimal Microsoft Graph + token endpoint double driven through httpx's mock transport."""

    def __init__(self, *, rotate_to: str | None = "ms-rt-2", fail_send: bool = False):
        self.rotate_to = rotate_to
        self.fail_send = fail_send
        self.sent: list[dict] = []
        self.token_calls = 0
        self.attachment = base64.b64encode(b"SHIPPING INSTRUCTION\nShipper: APRIL FAR EAST (M) SDN BHD\n").decode()

    def handler(self, request: httpx.Request) -> httpx.Response:
        url, path = str(request.url), request.url.path
        if "oauth2/v2.0/token" in url:
            self.token_calls += 1
            body = {"access_token": f"access-{self.token_calls}", "expires_in": 3600, "scope": "Mail.Read Mail.Send"}
            if self.rotate_to:
                body["refresh_token"] = self.rotate_to
            return httpx.Response(200, json=body)
        if request.headers.get("Authorization") != f"Bearer access-{self.token_calls}":
            return httpx.Response(401, json={"error": "expired"})
        if path.endswith("/me/mailFolders/inbox/messages"):
            return httpx.Response(200, json={"value": [{
                "id": "AAMk1", "internetMessageId": "<abc-123@contoso.com>", "conversationId": "conv-1",
                "from": {"emailAddress": {"name": "Docs Team", "address": "docs@vitalsolutions.sg"}},
                "toRecipients": [{"emailAddress": {"address": "ops@contoso.com"}}], "ccRecipients": [],
                "subject": "REQUEST BL DRAFT _ PO 1", "body": {"contentType": "html", "content": "<p>Attached are the <b>SI</b> and draft BL.</p><script>x()</script>"},
                "receivedDateTime": "2026-09-20T08:15:00Z", "hasAttachments": True,
            }]})
        if path.endswith("/me/messages/AAMk1/attachments"):
            return httpx.Response(200, json={"value": [
                {"@odata.type": "#microsoft.graph.fileAttachment", "name": "PO1_SI.txt", "size": 60, "contentBytes": self.attachment},
                {"@odata.type": "#microsoft.graph.itemAttachment", "name": "forwarded item"},
            ]})
        if path.endswith("/me/sendMail"):
            if self.fail_send:
                return httpx.Response(500, json={"error": "boom"})
            self.sent.append(json.loads(request.content))
            return httpx.Response(202, headers={"request-id": "req-77"})
        return httpx.Response(404, json={"error": path})


def _patch_httpx(monkeypatch, graph: _Graph) -> None:
    transport = httpx.MockTransport(graph.handler)

    def fake_request(method, url, **kwargs):
        with httpx.Client(transport=transport) as c:
            return c.request(method, url, **{k: v for k, v in kwargs.items() if k != "timeout"})

    def fake_post(url, **kwargs):
        return fake_request("POST", url, **kwargs)

    monkeypatch.setattr(connectors.httpx, "request", fake_request)
    monkeypatch.setattr(connectors.httpx, "post", fake_post)


def test_graph_connector_fetches_normalises_and_rotates_refresh_token(monkeypatch):
    graph = _Graph()
    _patch_httpx(monkeypatch, graph)
    rotated: list[str] = []
    conn = MicrosoftGraphConnector(client_id="id", client_secret="secret", refresh_token="ms-rt-1", address="ops@contoso.com", on_refresh_token=rotated.append)
    messages = list(conn.fetch(limit=5))
    assert len(messages) == 1
    msg = messages[0]
    assert msg.provider == "outlook" and msg.raw["email_id"] == "abc-123_contoso.com" and msg.raw["from"] == "docs@vitalsolutions.sg" and msg.raw["from_name"] == "Docs Team"
    assert msg.raw["body"] == "Attached are the SI and draft BL." and msg.raw["to"] == ["ops@contoso.com"] and msg.raw["conversation_id"] == "conv-1"
    assert msg.raw["attachments"] == ["attachments/PO1_SI.txt"] and b"APRIL FAR EAST" in msg.blobs["attachments/PO1_SI.txt"]
    assert msg.received_at == datetime(2026, 9, 20, 8, 15)
    assert rotated == ["ms-rt-2"] and conn.refresh_token == "ms-rt-2"

    result = conn.send(["docs@vitalsolutions.sg"], "RE: PO 1", "Please correct the consignee.", ["cc@contoso.com"])
    assert result["status"] == 202 and result["id"] == "req-77" and result["from"] == "ops@contoso.com"
    payload = graph.sent[0]["message"]
    assert payload["subject"] == "RE: PO 1" and payload["toRecipients"][0]["emailAddress"]["address"] == "docs@vitalsolutions.sg" and payload["ccRecipients"][0]["emailAddress"]["address"] == "cc@contoso.com"
    assert graph.token_calls == 1, "access token is cached between calls"

    graph.fail_send = True
    with pytest.raises(RuntimeError, match="sendMail"):
        conn.send(["a@b.co"], "s", "b")


def test_connector_for_mailbox_dispatches_by_provider(monkeypatch):
    _ms_env(monkeypatch)
    outlook = UserMailbox(user_id="u_ops_1", provider="outlook", address="a@contoso.com", refresh_token_enc=encrypt_token("rt"), scopes=["Mail.Read"], connected_at=datetime.utcnow())
    conn = connector_for_mailbox(outlook)
    assert isinstance(conn, MicrosoftGraphConnector) and conn.address == "a@contoso.com" and conn.tenant == "common"
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "g"); monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "s")
    gmail = UserMailbox(user_id="u_ops_1", provider="gmail", address="a@gmail.com", refresh_token_enc=encrypt_token("rt"), scopes=[], connected_at=datetime.utcnow())
    assert connector_for_mailbox(gmail).name == "gmail"
    weird = UserMailbox(user_id="u_ops_1", provider="yahoo", address="a@y.com", refresh_token_enc=encrypt_token("rt"), scopes=[], connected_at=datetime.utcnow())
    from app.config import ConfigurationError

    with pytest.raises(ConfigurationError):
        connector_for_mailbox(weird)


# ---------------------------------------------------------------- end to end through the desk
def test_outlook_mailbox_fetch_tags_case_and_reply_leaves_from_outlook(monkeypatch):
    _ms_env(monkeypatch)
    monkeypatch.setenv("EMAIL_PROVIDER", "none")
    monkeypatch.setenv("EMAIL_SEND_MODE", "live")
    graph = _Graph(rotate_to="ms-rt-next")
    _patch_httpx(monkeypatch, graph)
    repo = MemoryRepository()
    service = CaseService(repo)
    mailbox = UserMailbox(user_id="u_ops_1", provider="outlook", address="ops@contoso.com", refresh_token_enc=encrypt_token("ms-rt-1"),
                          scopes=["Mail.Read", "Mail.Send", "offline_access"], connected_at=datetime.utcnow())
    repo.save_mailbox(mailbox)
    result = poll_user_mailbox(service, mailbox, actor_id="u_ops_1", limit=5)
    assert result["mailbox"] == "ops@contoso.com" and len(result["created"]) == 1
    assert decrypt_token(repo.get_mailbox("u_ops_1").refresh_token_enc) == "ms-rt-next", "rotated token persisted"
    case = repo.get_case(result["created"][0])
    email = repo.get_email(case.source_email_id)
    assert email.provider == "outlook" and email.mailbox_address == "ops@contoso.com" and email.sender == "docs@vitalsolutions.sg"
    assert case.drafts, "SI without BL -> missing-document request draft"
    approved = service.approve_draft(case.id, DraftDecision(draft_id=case.drafts[0].id), repo.get_user("u_sup_1"))
    assert approved.drafts[0].status == DraftStatus.SENT
    assert graph.sent and graph.sent[0]["message"]["toRecipients"][0]["emailAddress"]["address"] == "docs@vitalsolutions.sg"
    sent_event = [e for e in repo.list_audit(case.id) if e.action == "NOTIFICATION_SENT"][-1]
    assert sent_event.after["from"] == "ops@contoso.com" and sent_event.after["mode"] == "gmail"


def test_connect_storage_failure_returns_to_the_page_not_login(monkeypatch):
    """A failed Connect must not bounce a signed-in user through /login (where the error was never shown)."""
    _ms_env(monkeypatch)
    token = _login(monkeypatch, email="storage@contoso.com")
    start = client.get("/auth/microsoft/start", params={"intent": "connect", "next": "/welcome"}, headers={"Authorization": f"Bearer {token}"}).json()
    state = parse_qs(urlparse(start["url"]).query)["state"][0]
    _fake_ms(monkeypatch, email="storage@contoso.com", scopes="Mail.Read Mail.Send offline_access User.Read", refresh="rt")
    from app.repositories.supabase_repo import MailboxStorageMissing

    def boom(self, mailbox):
        raise MailboxStorageMissing("user_mailboxes table missing")
    monkeypatch.setattr(type(get_repo()), "save_mailbox", boom)
    r = client.get("/auth/microsoft/callback", params={"code": "c", "state": state})
    assert r.status_code == 302 and r.headers["location"] == "http://localhost:3000/welcome?mailbox_error=mailbox_table_missing"
    # a *login* failure still lands on /login
    state2 = parse_qs(urlparse(client.get("/auth/microsoft/start", params={"intent": "login"}).json()["url"]).query)["state"][0]
    monkeypatch.setattr(ms_auth, "_exchange_code", lambda code: (_ for _ in ()).throw(RuntimeError("no")))
    assert client.get("/auth/microsoft/callback", params={"code": "c", "state": state2}).headers["location"].endswith("/login?error=exchange_failed")
    assert client.get("/auth/config").json()["mailbox_storage_ready"] is True
    assert client.get("/health").json()["migrations"]["user_mailboxes"] is True
