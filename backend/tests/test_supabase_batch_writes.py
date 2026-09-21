"""SupabaseRepository.batch_writes(): one processing run = one flush (projections once, audits in bulk, policy read once)."""
from __future__ import annotations

import os
import threading
from datetime import datetime

import pytest

os.environ.setdefault("REPO_BACKEND", "memory")

from app.contracts.schemas import (  # noqa: E402
    ActorType, AuditEvent, CaseRecord, CaseStatus, EmailMessage, HackathonCategory, Intent, IntentClassification, PolicyRecord, Priority,
    SecurityAssessment, SecurityOutcome,
)
from app.repositories.supabase_repo import SupabaseRepository  # noqa: E402


class _Query:
    """Records every table operation as (table, op) and answers reads with canned rows."""

    def __init__(self, log: list, table: str, rows: dict) -> None:
        self.log, self.table, self.rows, self.payload = log, table, rows, None

    def _op(self, name, payload=None):
        self.log.append((self.table, name, payload))
        self.payload = payload
        return self

    def upsert(self, payload): return self._op("upsert", payload)
    def insert(self, payload): return self._op("insert", payload)
    def delete(self): return self._op("delete")
    def select(self, *a, **k): return self._op("select")
    def eq(self, *a): return self
    def order(self, *a, **k): return self
    def limit(self, *a): return self

    def execute(self):
        class R:
            pass
        r = R()
        r.data = self.rows.get(self.table, [])
        return r


class _Client:
    def __init__(self) -> None:
        self.log: list = []
        self.rows: dict = {"policy_versions": [{"id": "pol_1", "version": "v7", "name": "n", "values": {"x": 1}, "updated_by": "u", "created_at": "2026-09-22T00:00:00"}]}

    def table(self, name):
        return _Query(self.log, name, self.rows)


def _repo() -> SupabaseRepository:
    repo = object.__new__(SupabaseRepository)
    repo.client = _Client()
    repo.tenant = "tenant_april"
    repo._lock = threading.RLock()
    repo._cases, repo._cases_by_id, repo._emails, repo._emails_by_id, repo._audit_recent = None, {}, None, {}, None
    return repo


def _case(cid: str) -> CaseRecord:
    cls = IntentClassification(intent=Intent.DOCUMENT_VERIFICATION, hackathon_category=HackathonCategory.BL_COMPARISON, action_required=True,
                               priority=Priority.MEDIUM, confidence=0.9, rationale="test")
    return CaseRecord(id=cid, source_email_id=f"em_{cid}", intent=cls.intent, hackathon_category=cls.hackathon_category, action_required=True,
                      priority=Priority.MEDIUM, status=CaseStatus.CLASSIFIED, security=SecurityAssessment(outcome=SecurityOutcome.SAFE, score=0.0), classification=cls)


def _email(eid: str) -> EmailMessage:
    return EmailMessage(id=eid, sender="a@b.co", recipients=["c@d.co"], subject="s", body="b", received_at=datetime.utcnow(), checksum="chk" + eid)


def _ev(i: int, cid: str = "case_a") -> AuditEvent:
    return AuditEvent(event_id=f"evt_{i}", case_id=cid, timestamp=datetime.utcnow(), actor_type=ActorType.SYSTEM, actor_id="agent", action=f"STEP_{i}")


def _ops(client, table=None, op=None):
    return [(t, o) for t, o, _ in client.log if (table is None or t == table) and (op is None or o == op)]


def test_outside_a_batch_every_write_goes_straight_to_the_database():
    repo = _repo()
    repo.save_case(_case("case_a"))
    assert _ops(repo.client, "cases", "upsert") == [("cases", "upsert")]
    assert len(_ops(repo.client, op="delete")) == 7, "child projections are rewritten on every save outside a batch"
    repo.append_audit(_ev(1))
    assert _ops(repo.client, "audit_events") == [("audit_events", "insert")]


def test_inside_a_batch_projections_and_audits_flush_once_in_order():
    repo = _repo()
    with repo.batch_writes():
        for _ in range(4):
            repo.save_case(_case("case_a"))     # the agent saves the same case many times per run
        repo.save_case(_case("case_b"))
        repo.save_email(_email("em_1"))
        repo.save_email(_email("em_1"))
        for i in range(5):
            repo.append_audit(_ev(i))
        assert repo.get_active_policy().version == "v7" and repo.get_active_policy().version == "v7"
        # while open: canonical rows are written immediately (reads stay fresh), nothing else is
        assert len(_ops(repo.client, "cases", "upsert")) == 5
        assert _ops(repo.client, op="delete") == [] and _ops(repo.client, "audit_events") == [] and _ops(repo.client, "email_messages") == []
        assert len(_ops(repo.client, "policy_versions", "select")) == 1, "policy is read once per run"
        assert repo.get_case("case_b") is not None and repo.get_email("em_1") is not None, "in-memory caches are updated at save time"
    # on close: 7 deletes per distinct case, one bulk audit insert, one email write
    assert len(_ops(repo.client, op="delete")) == 14
    audits = [p for t, o, p in repo.client.log if t == "audit_events" and o == "insert"]
    assert len(audits) == 1 and [row["event_id"] for row in audits[0]] == [f"evt_{i}" for i in range(5)]
    assert len(_ops(repo.client, "email_messages", "upsert")) == 1
    # after close: back to immediate writes
    repo.append_audit(_ev(9))
    assert len([p for t, o, p in repo.client.log if t == "audit_events"]) == 2


def test_a_crashing_run_still_flushes_its_audit_trail_and_nested_batches_share_one_flush():
    repo = _repo()
    with pytest.raises(RuntimeError):
        with repo.batch_writes():
            repo.append_audit(_ev(1))
            with repo.batch_writes():           # nested (pipeline inside agent): no extra flush
                repo.append_audit(_ev(2))
                repo.save_case(_case("case_a"))
            assert _ops(repo.client, "audit_events") == [], "the inner context must not flush"
            raise RuntimeError("graph exploded")
    audits = [p for t, o, p in repo.client.log if t == "audit_events" and o == "insert"]
    assert len(audits) == 1 and [r["event_id"] for r in audits[0]] == ["evt_1", "evt_2"]
    assert len(_ops(repo.client, op="delete")) == 7


def test_saving_a_policy_inside_a_batch_invalidates_the_memo():
    repo = _repo()
    with repo.batch_writes():
        assert repo.get_active_policy().version == "v7"
        repo.client.rows["policy_versions"] = [{**repo.client.rows["policy_versions"][0], "id": "pol_2", "version": "v8"}]
        assert repo.get_active_policy().version == "v7", "memoised within the run"
        repo.save_policy_version(PolicyRecord(id="pol_2", version="v8", name="n", values={}, updated_by="u", updated_at=datetime.utcnow()))
        assert repo.get_active_policy().version == "v8"


def test_batches_are_per_thread():
    repo = _repo()
    seen: dict[str, int] = {}
    with repo.batch_writes():
        def other():
            repo.append_audit(_ev(5, "case_t"))    # another thread has no batch: writes immediately
            seen["inserts"] = len(_ops(repo.client, "audit_events", "insert"))
        t = threading.Thread(target=other)
        t.start(); t.join()
    assert seen["inserts"] == 1
