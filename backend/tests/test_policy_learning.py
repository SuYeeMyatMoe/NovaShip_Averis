"""Security gate self-learning: archived flagged mail -> blocked-sender suggestion -> Admin accepts into a policy version."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("REPO_BACKEND", "memory")
os.environ.setdefault("AUTO_SEED", "0")
os.environ.setdefault("AUTH_MODE", "demo")
os.environ.setdefault("EMAIL_SEND_MODE", "simulate")
os.environ.setdefault("LLM_PROVIDER", "none")

from fastapi.testclient import TestClient  # noqa: E402

from app.ai.policy_learning import sender_bucket  # noqa: E402
from app.ai.security_precheck import assess_security  # noqa: E402
from app.config import get_repo  # noqa: E402
from app.contracts.schemas import EmailMessage  # noqa: E402
from app.core.policy import DEFAULT_POLICY  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)
SUP = {"X-User-Id": "u_sup_1"}
OPS = {"X-User-Id": "u_ops_1"}
ADM = {"X-User-Id": "u_admin_1"}

LURE_SENDER = "deals@prize-claim.net"          # lure keyword in the domain -> SUSPICIOUS at the gate
_counter = {"n": 0}


def _ingest(sender: str, subject: str = "Container schedule update", body: str = "Please see the updated schedule for your booking.", atts: dict[str, str] | None = None) -> str:
    _counter["n"] += 1
    payload = {"email_id": f"learn_{_counter['n']:03d}", "from": sender, "subject": f"{subject} {_counter['n']}", "body": body, "provider": "test",
               "attachments": [{"name": n, "content_base64": __import__("base64").b64encode(t.encode()).decode()} for n, t in (atts or {}).items()]}
    r = client.post("/webhooks/email", json=payload, headers=SUP)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _case(cid: str) -> dict:
    return client.get(f"/cases/{cid}", headers=SUP).json()


def _suggestions(headers=ADM) -> dict:
    r = client.get("/policies/suggestions", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _reset_policy():
    repo = get_repo()
    repo.policies[:] = repo.policies[:1]


def test_flagged_sender_is_suspicious_and_archiving_removes_it_from_the_open_queue():
    cid = _ingest(LURE_SENDER)
    assert _case(cid)["security"]["outcome"] == "SUSPICIOUS"
    open_ids = {r["case_id"] for r in client.get("/security/queue", headers=SUP).json()["items"]}
    assert cid in open_ids
    assert client.post(f"/cases/{cid}/complete", json={"note": "junk"}, headers=SUP).status_code == 200
    q = client.get("/security/queue", headers=SUP).json()
    assert cid not in {r["case_id"] for r in q["items"]} and q["counts"]["handled"] >= 1
    handled = client.get("/security/queue", params={"state": "handled"}, headers=SUP).json()["items"]
    row = next(r for r in handled if r["case_id"] == cid)
    assert row["gate_state"] == "archived"
    assert cid in {r["case_id"] for r in client.get("/security/queue", params={"state": "all"}, headers=SUP).json()["items"]}


def test_three_archives_open_a_block_sender_suggestion_and_two_only_show_progress():
    sender = "offers@crypto-parcel.io"
    for _ in range(2):
        cid = _ingest(sender)
        assert client.post(f"/cases/{cid}/complete", json={"note": "junk"}, headers=SUP).status_code == 200
    s = _suggestions()
    assert all(x["bucket"] != "crypto-parcel.io" for x in s["items"])
    assert any(p["bucket"] == "crypto-parcel.io" and p["count"] == 2 and p["needed"] == 3 for p in s["progress"])

    cid = _ingest(sender)
    assert client.post(f"/cases/{cid}/complete", json={"note": "junk"}, headers=SUP).status_code == 200
    s = _suggestions()
    sug = next(x for x in s["items"] if x["bucket"] == "crypto-parcel.io")
    assert sug["recipe"] == "block_sender" and sug["count"] == 3 and sug["section"] == "security" and sug["key"] == "blocked_senders"
    assert "crypto-parcel.io" in sug["to_value"] and "crypto-parcel.io" in sug["proposed_section"]["blocked_senders"]
    assert len(sug["evidence"]) == 3 and all(e["outcome"] == "SUSPICIOUS" for e in sug["evidence"])
    assert sug["proposed_section"]["partner_domains"] == DEFAULT_POLICY["security"]["partner_domains"], "the whole section is proposed, nothing else changed"
    # operations staff may read suggestions (view_policy) but not decide
    assert client.get("/policies/suggestions", headers=OPS).status_code == 200
    assert client.post(f"/policies/suggestions/{sug['id']}/dismiss", headers=OPS).status_code == 403


def test_sender_sent_to_human_review_is_never_proposed_and_no_action_does_not_count():
    sender = "news@verify-invest.biz"
    for _ in range(3):
        cid = _ingest(sender)
        assert client.post(f"/cases/{cid}/complete", headers=SUP).status_code == 200
    assert any(x["bucket"] == "verify-invest.biz" for x in _suggestions()["items"])
    cid = _ingest(sender)
    assert client.post(f"/cases/{cid}/request-review", json={"note": "looks legit"}, headers=SUP).status_code == 200
    assert all(x["bucket"] != "verify-invest.biz" for x in _suggestions()["items"]), "a conflicting human decision cancels the suggestion"

    other = "promo@claim-prize.org"
    for _ in range(3):
        cid = _ingest(other)
        assert client.post(f"/cases/{cid}/no-action", headers=SUP).status_code == 200
    s = _suggestions()
    assert all(x["bucket"] != "claim-prize.org" for x in s["items"]) and all(p["bucket"] != "claim-prize.org" for p in s["progress"])


def test_freemail_sender_is_blocked_by_address_not_domain():
    assert sender_bucket("spammer@gmail.com") == "spammer@gmail.com" and sender_bucket("docs@vitalsolutions.sg") == "vitalsolutions.sg"
    sender = "prize.claims.desk@gmail.com"
    for _ in range(3):
        # one spam phrase (+0.25) and an unsupported attachment type (+0.1) -> SUSPICIOUS, the band a person has to judge
        cid = _ingest(sender, body="Congratulations, your parcel is waiting. See the attached voucher.", atts={"voucher.zip": "zip"})
        assert _case(cid)["security"]["outcome"] == "SUSPICIOUS"
        assert client.post(f"/cases/{cid}/complete", headers=SUP).status_code == 200
    s = _suggestions()
    sug = next(x for x in s["items"] if x["bucket"] == sender)
    assert sender in sug["to_value"] and "gmail.com" not in sug["to_value"]
    assert all("gmail.com" not in x["to_value"] for x in s["items"])


def test_dismiss_is_remembered_and_accept_blocks_future_mail():
    try:
        sender_a, sender_b = "win@parcel-prize.net", "alerts@crypto-claim.co"
        for sender in (sender_a, sender_b):
            for _ in range(3):
                cid = _ingest(sender)
                assert client.post(f"/cases/{cid}/complete", headers=SUP).status_code == 200
        items = {x["bucket"]: x for x in _suggestions()["items"]}
        a, b = items["parcel-prize.net"], items["crypto-claim.co"]

        r = client.post(f"/policies/suggestions/{a['id']}/dismiss", json={"note": "keep watching"}, headers=ADM)
        assert r.status_code == 200 and all(x["id"] != a["id"] for x in r.json()["items"])
        assert all(x["id"] != a["id"] for x in _suggestions()["items"])
        assert client.post(f"/policies/suggestions/{a['id']}/dismiss", headers=ADM).status_code == 404
        assert any(e.action == "POLICY_SUGGESTION_DISMISSED" and e.after["suggestion_id"] == a["id"] for e in get_repo().list_audit(None))

        versions_before = len(client.get("/policies", headers=ADM).json()["versions"])
        r = client.put("/policies", json={"security": b["proposed_section"], "change_note": f"Learned: {b['title']}", "accepted_suggestions": [b["id"]]}, headers=ADM)
        assert r.status_code == 200 and r.json()["active"]["version"] == f"v{versions_before + 1}"
        pol = client.get("/policies", headers=ADM).json()
        assert "crypto-claim.co" in pol["effective"]["security"]["blocked_senders"]
        assert any("blocked" in line for line in pol["explanation"])
        audit = get_repo().list_audit(None)
        assert any(e.action == "POLICY_SUGGESTION_ACCEPTED" and e.after["suggestion_id"] == b["id"] for e in audit)
        assert any(e.action == "POLICY_UPDATED" and e.after.get("accepted_suggestions") == [b["id"]] for e in audit)
        assert all(x["bucket"] != "crypto-claim.co" for x in _suggestions()["items"]), "once in the policy the recipe no longer fires"

        cid = _ingest(sender_b)
        case = _case(cid)
        assert case["security"]["outcome"] == "SPAM" and any(s["signal"] == "BLOCKED_SENDER" for s in case["security"]["signals"])
        assert case["status"] == "NO_ACTION_INFO" and not case["drafts"]
        assert cid not in {r["id"] for r in client.get("/cases", params={"attention": "yes", "agent": "any", "limit": 500}, headers=SUP).json()["items"]}
        assert cid not in {r["case_id"] for r in client.get("/security/queue", headers=SUP).json()["items"]}
    finally:
        _reset_policy()


def test_assess_security_blocked_list_forces_spam_and_defaults_are_unchanged():
    clean = EmailMessage(id="e1", provider="test", provider_message_id="m1", sender="ops@vitalsolutions.sg", recipients=["desk@aprilasia.com"], subject="Draft BL", body="Attached is the draft BL for your review.", received_at=__import__("datetime").datetime.utcnow())
    policy = dict(DEFAULT_POLICY["security"])
    assert assess_security(clean, [], policy).outcome.value == "SAFE"
    assert assess_security(clean, [], {**policy, "blocked_senders": ["vitalsolutions.sg"]}).outcome.value == "SPAM"
    by_address = assess_security(clean, [], {**policy, "blocked_senders": ["OPS@vitalsolutions.sg"]})
    assert by_address.outcome.value == "SPAM" and any(s.signal == "BLOCKED_SENDER" for s in by_address.signals)
    assert assess_security(clean, [], {**policy, "blocked_senders": ["someone-else.com"]}).outcome.value == "SAFE"
