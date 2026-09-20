"""Login / register / logout: session tokens, RBAC through sessions, audit of auth events."""
import os

from fastapi.testclient import TestClient

os.environ.setdefault("REPO_BACKEND", "memory")
os.environ["AUTO_SEED"] = "0"
os.environ["LLM_PROVIDER"] = "none"

from app.auth.accounts import DEMO_PASSWORD, hash_password, verify_password  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)


def _login(email: str, password: str = DEMO_PASSWORD):
    return client.post("/auth/login", json={"email": email, "password": password})


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_password_hashing_is_salted_and_verifies():
    h1, h2 = hash_password("Secret123!"), hash_password("Secret123!")
    assert h1 != h2 and h1.startswith("pbkdf2_sha256$")
    assert verify_password("Secret123!", h1) and not verify_password("wrong", h1) and not verify_password("x", None)


def test_unauthenticated_request_is_401_not_default_user():
    assert client.get("/me").status_code == 401
    assert client.get("/cases").status_code == 401


def test_demo_accounts_log_in_and_carry_their_roles():
    r = _login("hari_mardianto@aprilasia.com")
    assert r.status_code == 200
    body = r.json()
    assert body["token"].startswith("nsa.") and body["user"]["id"] == "u_sup_1" and "SUPERVISOR" in body["user"]["roles"]
    assert "approve_send" in body["user"]["permissions"] and "edit_policy" not in body["user"]["permissions"]
    me = client.get("/me", headers=_bearer(body["token"]))
    assert me.status_code == 200 and me.json()["id"] == "u_sup_1"
    assert client.get("/auth/session", headers=_bearer(body["token"])).json()["display_name"] == "Hari Mardianto"


def test_wrong_password_is_rejected_and_audited():
    r = _login("hari_mardianto@aprilasia.com", "nope-nope")
    assert r.status_code == 401
    audit = client.get("/audit", headers={"X-User-Id": "u_admin_1"}).json()
    assert any(e["action"] == "LOGIN_FAILED" and e["actor_id"] == "u_sup_1" for e in audit["events"])


def test_session_respects_rbac():
    ops = _login("hanna_azhari@aprilasia.com").json()["token"]
    assert client.get("/cases", headers=_bearer(ops)).status_code == 200
    assert client.get("/audit", headers=_bearer(ops)).status_code == 403          # ops cannot read global audit
    assert client.put("/policies", json={"human_review": {"confidence_below": 0.9}}, headers=_bearer(ops)).status_code == 403


def test_policies_are_for_ops_supervisor_admin_not_auditor():
    aud = _login("sokyong_ooi@aprilasia.com").json()["token"]
    assert client.get("/policies", headers=_bearer(aud)).status_code == 403
    assert client.put("/policies", json={"human_review": {"confidence_below": 0.9}}, headers=_bearer(aud)).status_code == 403
    ops = _login("hanna_azhari@aprilasia.com").json()["token"]
    assert client.get("/policies", headers=_bearer(ops)).status_code == 200
    assert "view_policy" in client.get("/me", headers=_bearer(ops)).json()["permissions"]
    assert "view_policy" not in client.get("/me", headers=_bearer(aud)).json()["permissions"]


def test_logout_revokes_the_session():
    token = _login("eileen_teo@aprilasia.com").json()["token"]
    assert client.post("/auth/logout", headers=_bearer(token)).status_code == 200
    r = client.get("/me", headers=_bearer(token))
    assert r.status_code == 401 and r.json()["detail"]["category"] == "AUTH_ERROR"
    assert client.get("/me", headers=_bearer("nsa.tampered.token")).status_code == 401


def test_register_creates_least_privilege_account(monkeypatch):
    monkeypatch.setenv("REGISTER_ALLOWED_ROLES", "OPERATIONS_STAFF")
    cfg = client.get("/auth/config").json()
    assert cfg["register_roles"] == ["OPERATIONS_STAFF"]
    assert client.post("/auth/register", json={"email": "not-an-email", "password": "Secret123!", "display_name": "Nope"}).status_code == 400
    assert client.post("/auth/register", json={"email": "new.user@aprilasia.com", "password": "short", "display_name": "New User"}).status_code == 400
    assert client.post("/auth/register", json={"email": "new.user@aprilasia.com", "password": "Secret123!", "display_name": "New User", "role": "ADMIN"}).status_code == 403
    r = client.post("/auth/register", json={"email": "New.User@aprilasia.com", "password": "Secret123!", "display_name": "New User"})
    assert r.status_code == 201
    u = r.json()["user"]
    assert u["email"] == "new.user@aprilasia.com" and u["roles"] == ["OPERATIONS_STAFF"] and "approve_send" not in u["permissions"]
    assert client.post("/auth/register", json={"email": "new.user@aprilasia.com", "password": "Secret123!", "display_name": "Dup"}).status_code == 409
    again = _login("new.user@aprilasia.com", "Secret123!")
    assert again.status_code == 200 and again.json()["user"]["id"] == u["id"]
    assert client.get("/cases", headers=_bearer(again.json()["token"])).status_code == 200


def test_register_admin_when_env_allows_joins_shared_desk(monkeypatch):
    monkeypatch.setenv("REGISTER_ALLOWED_ROLES", "ADMIN,SUPERVISOR,OPERATIONS_STAFF")
    cfg = client.get("/auth/config").json()
    assert cfg["register_roles"][0] == "ADMIN" and "SUPERVISOR" in cfg["register_roles"]
    r = client.post("/auth/register", json={"email": "judge.demo@gmail.com", "password": "Secret123!", "display_name": "Judge Demo", "role": "ADMIN"})
    assert r.status_code == 201
    u = r.json()["user"]
    assert u["email"] == "judge.demo@gmail.com" and u["roles"] == ["ADMIN"]
    assert "edit_policy" in u["permissions"] and "approve_send" in u["permissions"]
    mine = client.get("/cases", headers=_bearer(r.json()["token"]))
    shared = client.get("/cases", headers={"X-User-Id": "u_admin_1"})
    assert mine.status_code == 200 and shared.status_code == 200
    assert mine.json()["total"] == shared.json()["total"]
