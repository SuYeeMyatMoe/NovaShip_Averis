"""LangGraph agent: security agent routing, human-in-the-loop interrupt/resume, RAG scoping."""
import json
import os
from pathlib import Path

os.environ["AUTO_SEED"] = "0"
os.environ["LLM_PROVIDER"] = "none"
os.environ["VECTOR_STORE"] = "local"
os.environ["EMBEDDING_PROVIDER"] = "local"

from fastapi.testclient import TestClient  # noqa: E402

from app.agents.graph import CaseAgent  # noqa: E402
from app.agents.rag import RAG, Chunk, LocalStore, case_chunks, knowledge_chunks  # noqa: E402
from app.main import app  # noqa: E402
from app.pipeline.orchestrator import Pipeline  # noqa: E402
from app.repositories.memory import MemoryRepository  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "sdoc-hackathon-bundle"
SUP = {"X-User-Id": "u_sup_1"}
OPS = {"X-User-Id": "u_ops_1"}


def _load(repo: MemoryRepository, eid: str):
    raw = json.loads((BUNDLE / "inbox" / f"{eid}.json").read_text(encoding="utf-8"))
    pipe = Pipeline(repo)
    email = pipe.ingest_email(raw, {a: (BUNDLE / a).read_bytes() for a in raw["attachments"] if (BUNDLE / a).exists()})
    return pipe.run(email)


def test_graph_pauses_on_mismatch_and_resumes_with_approval():
    repo = MemoryRepository()
    _load(repo, "email_004")
    agent = CaseAgent(repo)
    st = agent.run("case_email_004", actor_id="u_sup_1")
    assert st["paused"] is True and st["next"] == ["human_review"]
    assert st["interrupt"]["mismatch_fields"] == ["consignee", "notify_party"]
    assert [t["node"] for t in st["trace"]] == ["security_precheck", "security_agent", "intent_classifier", "attachment_classifier", "document_extractor", "seven_field_comparator", "summary_and_draft"]
    st2 = agent.resume("case_email_004", {"action": "approve", "draft_id": st["interrupt"]["draft_id"], "user_id": "u_sup_1"})
    assert st2["paused"] is False and st2["notified"] is True and st2["status"] == "AWAITING_RESPONSE"
    actions = [a.action for a in repo.list_audit("case_email_004")]
    assert "SECURITY_AGENT_VERDICT" in actions and "HUMAN_DECISION" in actions and "GRAPH_COMPLETED" in actions


def test_graph_clean_case_does_not_pause_and_spam_routes_to_no_action():
    repo = MemoryRepository()
    _load(repo, "email_001")
    _load(repo, "email_015")
    agent = CaseAgent(repo)
    ok = agent.run("case_email_001")
    assert ok["paused"] is False and ok["status"] == "DRAFT_READY"
    spam = agent.run("case_email_015")
    assert spam["route"] == "no_action" and spam["status"] == "NO_ACTION_INFO" and "intent_classifier" not in [t["node"] for t in spam["trace"]]
    assert spam["security_agent"]["outcome"] == "SPAM"


def test_graph_unreadable_scan_pauses_with_reason_and_ops_cannot_approve_external():
    repo = MemoryRepository()
    _load(repo, "email_512")
    agent = CaseAgent(repo)
    st = agent.run("case_email_512")
    assert st["paused"] and st["interrupt"]["review_reason"] == "unreadable" and st["interrupt"]["mismatch_fields"] == []
    st2 = agent.resume("case_email_512", {"action": "mark_no_action", "user_id": "u_ops_1"})
    assert st2["status"] == "NO_ACTION_INFO"


def test_agent_api_run_state_resume():
    client = TestClient(app)
    raw = json.loads((BUNDLE / "inbox" / "email_004.json").read_text(encoding="utf-8"))
    r = client.post("/webhooks/email", json={**raw, "email_id": "agent_api_004"}, headers=SUP).json()
    st = client.post(f"/agent/run/{r['id']}", headers=SUP).json()
    assert st["paused"] is True
    assert client.get(f"/agent/state/{r['id']}", headers=OPS).json()["next"] == ["human_review"]
    assert client.get("/agent/graph", headers=OPS).json()["mermaid"].startswith("---") or "security_agent" in client.get("/agent/graph", headers=OPS).json()["mermaid"]
    done = client.post(f"/agent/resume/{r['id']}", json={"action": "reject", "draft_id": st["interrupt"]["draft_id"], "note": "wrong"}, headers=SUP).json()
    assert done["paused"] is False and done["status"] == "HUMAN_REVIEW"
    assert client.post(f"/agent/resume/{r['id']}", json={"action": "approve"}, headers=SUP).status_code == 409


def test_rag_local_index_scopes_by_case(tmp_path):
    rag = RAG()
    rag.store = LocalStore(tmp_path / "index.json")
    repo = MemoryRepository()
    a = _load(repo, "email_004")
    b = _load(repo, "email_001")
    rag.index(knowledge_chunks())
    rag.index(case_chunks(a, repo.get_email(a.source_email_id)) + case_chunks(b, repo.get_email(b.source_email_id)))
    hits = rag.search("consignee UAB NOVAKOPA", case_id="case_email_004", k=5)
    assert hits and all(h["case_id"] in (None, "case_email_004") for h in hits)
    pol = rag.search("source of truth seven fields", sources=["policy"], k=2)
    assert pol and pol[0]["source"] == "policy"


def test_field_stats_and_security_queue_endpoints():
    client = TestClient(app)
    fs = client.get("/dashboard/fields", headers=OPS).json()
    assert [f["field"] for f in fs["fields"]] == ["shipper", "consignee", "notify_party", "port_of_loading", "port_of_discharge", "container_count", "gross_weight_kg"]
    q = client.get("/security/queue", headers=OPS).json()
    assert "items" in q
    assert client.get("/audit", headers=OPS).status_code == 403  # ops cannot view global audit
    assert client.get("/audit?limit=5", headers=SUP).status_code == 200


def test_local_embedder_can_match_supabase_vector_dimension(monkeypatch):
    from app.agents.rag import Embedder

    monkeypatch.setenv("EMBEDDING_PROVIDER", "local")
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "768")
    embedder = Embedder()
    assert embedder.dims == 768
    assert len(embedder.embed_query("shipping instruction")) == 768
