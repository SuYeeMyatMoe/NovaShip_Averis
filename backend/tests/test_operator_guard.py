"""Adaptive operator guard: limits learned from the audit log, policy-editable, surfaced as a warning payload."""
from __future__ import annotations

import base64
import os
from datetime import datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

os.environ.setdefault("REPO_BACKEND", "memory")
os.environ["AUTO_SEED"] = "0"
os.environ["LLM_PROVIDER"] = "none"

from app.ai import operator_behaviour as ob  # noqa: E402
from app.config import get_repo  # noqa: E402
from app.contracts.schemas import ActorType, AuditEvent  # noqa: E402
from app.core.policy import explain_policy, merged_policy  # noqa: E402
from app.main import app  # noqa: E402
from app.repositories.memory import MemoryRepository  # noqa: E402

client = TestClient(app)
ADMIN = {"X-User-Id": "u_admin_1"}


def _event(actor: str, action: str, at: datetime, case_id: str | None = "case_x") -> AuditEvent:
    return AuditEvent(event_id=f"evt_{uuid4().hex}", case_id=case_id, timestamp=at, actor_type=ActorType.USER, actor_id=actor, action=action)


def _seed_history(repo, actor: str, *, per_minute: int, minutes: int, start: datetime, action: str = "ASSIGNED") -> None:
    for m in range(minutes):
        for k in range(per_minute):
            repo.append_audit(_event(actor, action, start + timedelta(minutes=m, seconds=k)))


def test_guard_settings_come_from_policy_with_defaults():
    s = ob.guard_settings(None)
    assert s["auto_draft_after"] == 3 and s["burst_limit"] == 10 and s["adaptive"] is True
    s = ob.guard_settings({"operator_guard": {"auto_draft_after": "5", "burst_limit": 4, "adaptive": 0, "baseline_multiplier": "2.5"}})
    assert s["auto_draft_after"] == 5 and s["burst_limit"] == 4 and s["adaptive"] is False and s["baseline_multiplier"] == 2.5
    assert ob.guard_settings({"operator_guard": {"burst_limit": 0}})["burst_limit"] == 1
    text = " ".join(explain_policy(merged_policy({})))
    assert "Operator guard" in text and "AI privacy" in text


def test_baseline_learns_a_quiet_user_and_tightens_the_burst_limit():
    ob.clear_operator_burst()
    repo = MemoryRepository()
    now = datetime(2026, 9, 20, 10, 0, 0)
    # 25 working days, one action per minute for 2 minutes each day at 09:00 UTC -> median 1/min
    for day in range(25):
        _seed_history(repo, "u_quiet", per_minute=1, minutes=2, start=now - timedelta(days=day + 1, hours=1))
    settings = ob.guard_settings(None)
    base = ob.operator_baseline(repo, "u_quiet", settings, now=now)
    assert base["learned"] is True and base["events"] == 50 and base["median_per_min"] == 1.0
    assert base["effective_burst_limit"] == 3 and base["usual_hours_utc"] == [9, 9]

    # 3 actions inside one minute now trips the learned limit, well under the fixed 10
    for k in range(3):
        repo.append_audit(_event("u_quiet", "ASSIGNED", now - timedelta(seconds=10 - k)))
    warn = ob.burst_warning(repo, "u_quiet", settings=settings, now=now)
    assert warn and warn.signal == "OPERATOR_BURST_MUTATIONS" and "usual pace" in warn.evidence and "learned" in warn.evidence

    # a user with no history keeps the fixed limit
    thin = ob.operator_baseline(repo, "u_new", settings, now=now)
    assert thin["learned"] is False and thin["effective_burst_limit"] == 10
    for k in range(3):
        repo.append_audit(_event("u_new", "ASSIGNED", now - timedelta(seconds=10 - k)))
    assert ob.burst_warning(repo, "u_new", settings=settings, now=now) is None

    # adaptive off -> fixed limit even with history
    fixed = ob.guard_settings({"operator_guard": {"adaptive": False}})
    assert ob.operator_baseline(repo, "u_quiet", fixed, now=now)["effective_burst_limit"] == 10


def test_off_hours_warning_uses_learned_working_hours():
    repo = MemoryRepository()
    now = datetime(2026, 9, 20, 3, 0, 0)  # 03:00 UTC
    for day in range(25):
        _seed_history(repo, "u_day", per_minute=1, minutes=1, start=datetime(2026, 8, 25, 9, 0) + timedelta(days=day))
    settings = ob.guard_settings(None)
    late = ob.off_hours_warning(repo, "u_day", settings, now=now)
    assert late and late.signal == "OPERATOR_OFF_HOURS" and late.severity == "LOW"
    assert ob.off_hours_warning(repo, "u_day", settings, now=datetime(2026, 9, 20, 9, 30)) is None
    assert ob.off_hours_warning(repo, "u_day", ob.guard_settings({"operator_guard": {"off_hours_warning": False}}), now=now) is None


def test_burst_state_survives_a_fresh_service_and_uses_the_audit_log():
    """No in-process deque: a brand-new service instance sees the same window because it reads audit events."""
    from app.contracts.schemas import UserRecord
    from app.services.case_service import CaseService

    ob.clear_operator_burst()
    repo = MemoryRepository()
    from pathlib import Path

    repo.load_file(Path(__file__).resolve().parents[2] / "supabase" / "seed" / "snapshot.json")
    admin = repo.get_user("u_admin_1")
    case = next(c for c in repo.list_cases() if c.drafts)
    now = datetime.utcnow()
    for k in range(9):
        repo.append_audit(_event(admin.id, "ASSIGNED", now - timedelta(seconds=30 - k), case_id=case.id))
    fresh = CaseService(repo)
    from app.contracts.schemas import AssignRequest

    fresh.assign(case.id, AssignRequest(user_id="u_ops_1"), admin)
    warning = fresh.pop_operator_warning()
    assert warning and warning["signal"] == "OPERATOR_BURST_MUTATIONS" and warning["dialog"] is True
    assert fresh.pop_operator_warning() is None


def test_policy_edit_changes_auto_draft_threshold_and_warning_payload_is_returned():
    ob.clear_operator_burst()
    SI = "SHIPPING INSTRUCTION\nShipper: A CO\nConsignee: B CO\nNotify Party: B CO\nPort of Loading: PORT KLANG\nPort of Discharge: CALLAO\nContainer Count: 2 x 40'HC\nGross Weight (KG): 10,000 KG\n"
    BL = SI.replace("SHIPPING INSTRUCTION", "BILL OF LADING (DRAFT)").replace("2 x 40'HC", "3 x 40'HC")
    payload = {"email_id": "guard_policy_001", "from": "docs@vitalsolutions.sg", "subject": "TO CONFIRM DOCS _ guard", "body": "Attached are the SI and draft BL. Please confirm.",
               "attachments": [{"name": "g_SI.txt", "content_base64": base64.b64encode(SI.encode()).decode()}, {"name": "g_BL.txt", "content_base64": base64.b64encode(BL.encode()).decode()}]}
    cid = client.post("/webhooks/email", json=payload, headers=ADMIN).json()["id"]
    saved_policies = list(get_repo().policies)   # leave the shared in-memory policy history untouched for other test modules
    r = client.put("/policies", json={"operator_guard": {"auto_draft_after": 2, "adaptive": False}, "change_note": "tighter auto-draft"}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["active"]["values"]["operator_guard"]["auto_draft_after"] == 2
    try:
        seeded = client.get(f"/cases/{cid}", headers=ADMIN).json()
        rejected = client.post(f"/cases/{cid}/reject", json={"draft_id": seeded["drafts"][0]["id"], "note": "rework"}, headers=ADMIN).json()
        assert not any(a["signal"] == "AUTO_DRAFT_AFTER_REPEATED_ACTIONS" for a in rejected["anomalies"])
        second = client.post(f"/cases/{cid}/assign", json={"user_id": "u_ops_1"}, headers=ADMIN).json()
        assert any(a["signal"] == "AUTO_DRAFT_AFTER_REPEATED_ACTIONS" for a in second["anomalies"]), "threshold 2 -> draft after the second action"
        assert second["operator_warning"] and second["operator_warning"]["signal"] in {"AUTO_DRAFT_AFTER_REPEATED_ACTIONS", "OPERATOR_BURST_MUTATIONS"}
        assert second["operator_warning"]["dialog"] is True
        profile = client.get("/me/operator-profile", headers=ADMIN).json()
        assert profile["settings"]["auto_draft_after"] == 2 and profile["baseline"]["user_id"] == "u_admin_1" and "effective_burst_limit" in profile["baseline"]
    finally:
        get_repo().policies = saved_policies
        ob.clear_operator_burst()


def test_supabase_actor_query_filters_by_actor_and_time(monkeypatch):
    from app.repositories.supabase_repo import SupabaseRepository

    calls: list[tuple] = []

    class _Q:
        def __init__(self):
            self.ops = []

        def __getattr__(self, name):
            def _op(*args):
                self.ops.append((name, args))
                return self
            return _op

        def execute(self):
            calls.append(tuple(self.ops))
            return type("R", (), {"data": []})()

    repo = SupabaseRepository.__new__(SupabaseRepository)
    repo.tenant = "tenant_april"
    repo._t = lambda name: _Q()
    since = datetime(2026, 8, 1)
    assert repo.list_audit_for_actor("u_ops_1", since) == []
    ops = dict((op, args) for op, args in calls[0] if op in {"eq", "gte", "limit"} and args)
    flat = [args for op, args in calls[0] if op == "eq"]
    assert ("tenant_id", "tenant_april") in flat and ("actor_id", "u_ops_1") in flat
    assert ("timestamp", since.isoformat()) in [args for op, args in calls[0] if op == "gte"]
