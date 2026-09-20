"""Workbench: case autocomplete, parallel batch with per-case errors and retry, multi-case agent runs."""
from __future__ import annotations

import base64
import os

from fastapi.testclient import TestClient

os.environ.setdefault("REPO_BACKEND", "memory")
os.environ["AUTO_SEED"] = "0"
os.environ["LLM_PROVIDER"] = "none"

from app.main import app  # noqa: E402

client = TestClient(app)
SUP = {"X-User-Id": "u_sup_1"}
OPS = {"X-User-Id": "u_ops_1"}

SI = "SHIPPING INSTRUCTION\nShipper: APRIL FAR EAST (M) SDN BHD\nConsignee: MOORIM SP CO., LTD\nNotify Party: UAB NOVAKOPA\nPort of Loading: PORT KLANG (WESTPORT), MALAYSIA (MYPKG)\nPort of Discharge: CALLAO, PERU (PECLL)\nContainer Count: 3 x 40'HC\nGross Weight (KG): 22,000 KG\n"
BL_BAD = SI.replace("SHIPPING INSTRUCTION", "BILL OF LADING (DRAFT)").replace("3 x 40'HC", "4 x 40'HC")
BL_OK = SI.replace("SHIPPING INSTRUCTION", "BILL OF LADING (DRAFT)")


def _ingest(email_id: str, bl: str = BL_BAD, subject: str | None = None) -> str:
    payload = {"email_id": email_id, "from": "docs@vitalsolutions.sg", "subject": subject or f"REQUEST BL DRAFT _ {email_id}", "body": "Attached are the SI and draft BL. Please check the details and confirm.",
               "attachments": [{"name": f"{email_id}_SI.txt", "content_base64": base64.b64encode(SI.encode()).decode()}, {"name": f"{email_id}_BL.txt", "content_base64": base64.b64encode(bl.encode()).decode()}]}
    r = client.post("/webhooks/email", json=payload, headers=SUP)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_case_suggest_ranks_id_prefix_then_subject_and_sender():
    a = _ingest("wb_sug_001", subject="REQUEST BL DRAFT _ PO 77001_ ZEBRA BOARD")
    b = _ingest("wb_sug_002", subject="INVOICE for zebra shipment")
    r = client.get("/cases/suggest", params={"q": "wb_sug_00", "limit": 5}, headers=OPS)
    assert r.status_code == 200
    ids = [row["id"] for row in r.json()["items"]]
    assert ids[:2] == [b, a] or ids[:2] == [a, b]
    assert {"id", "subject", "sender", "status", "priority", "mismatch_count", "mailbox"} <= set(r.json()["items"][0])
    by_subject = [row["id"] for row in client.get("/cases/suggest", params={"q": "zebra"}, headers=OPS).json()["items"]]
    assert a in by_subject and b in by_subject
    assert client.get("/cases/suggest", params={"q": "docs@vitalsolutions"}, headers=OPS).json()["total"] >= 2
    assert client.get("/cases/suggest", params={"q": "nothing-matches-this"}, headers=OPS).json() == {"items": [], "total": 0}
    assert len(client.get("/cases/suggest", params={"limit": 3}, headers=OPS).json()["items"]) <= 3


def test_batch_runs_in_parallel_isolates_failures_and_retries_only_failed():
    ids = [_ingest(f"wb_par_{i:03d}") for i in range(4)]
    body = {"action": "compare", "case_ids": ids + ["case_does_not_exist"], "params": {"parallel": 4}}
    r = client.post("/cases/batch", json=body, headers=SUP)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["parallel"] == 4 and out["failed_ids"] == ["case_does_not_exist"]
    assert all(out["results"][cid]["ok"] and "ms" in out["results"][cid] for cid in ids)
    err = out["results"]["case_does_not_exist"]["error"]
    assert err["category"] and "retryable" in err
    for cid in ids:
        assert client.get(f"/cases/{cid}", headers=SUP).json()["mismatch_count"] == 1

    retry = client.post("/cases/batch", json={**body, "params": {"parallel": 2, "retry_failed": ["case_does_not_exist"]}}, headers=SUP).json()
    assert list(retry["results"]) == ["case_does_not_exist"] and retry["parallel"] == 2

    sequential = client.post("/cases/batch", json={"action": "compare", "case_ids": ids, "params": {"parallel": 1}}, headers=SUP).json()
    assert sequential["parallel"] == 1 and {cid: r["status"] for cid, r in sequential["results"].items()} == {cid: r["status"] for cid, r in out["results"].items() if cid in ids}
    audit = client.get("/audit", params={"action": "BATCH_ACTION"}, headers=SUP).json()["events"]
    assert any((e.get("after") or {}).get("parallel") == 4 and (e.get("after") or {}).get("failed") == 1 for e in audit)
    assert client.post("/cases/batch", json={"action": "compare", "case_ids": ids}, headers=OPS).status_code == 403


def test_agent_run_batch_reports_paused_and_errors_per_case_and_bulk_resume():
    paused_id = _ingest("wb_agent_001")
    clean_id = _ingest("wb_agent_002", bl=BL_OK)
    r = client.post("/agent/run-batch", json={"case_ids": [paused_id, clean_id, "case_missing"], "parallel": 3}, headers=SUP)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["parallel"] == 3 and out["failed_ids"] == ["case_missing"]
    assert out["results"][paused_id]["ok"] and out["results"][paused_id]["paused"] is True and out["results"][paused_id]["interrupt"]
    assert out["results"][clean_id]["ok"] and out["results"][clean_id]["paused"] is False
    assert out["results"]["case_missing"]["ok"] is False and out["results"]["case_missing"]["error"]["retryable"] is False
    assert paused_id in out["paused_ids"] and clean_id not in out["paused_ids"]

    denied = client.post("/agent/resume-batch", json={"action": "approve", "case_ids": [paused_id]}, headers=SUP)
    assert denied.status_code == 400 and "per case" in denied.json()["detail"]["error"]
    resumed = client.post("/agent/resume-batch", json={"action": "request_review", "case_ids": [paused_id, clean_id], "note": "bulk review"}, headers=SUP).json()
    assert resumed["results"][paused_id]["ok"] is True
    assert resumed["results"][clean_id]["ok"] is False and clean_id in resumed["failed_ids"]
    assert client.get(f"/agent/state/{paused_id}", headers=SUP).json()["paused"] is False
    assert client.post("/agent/run-batch", json={"case_ids": []}, headers=SUP).status_code == 400
    audit = client.get("/audit", params={"action": "AGENT_BATCH_RUN"}, headers=SUP).json()["events"]
    assert any((e.get("after") or {}).get("paused") == 1 and (e.get("after") or {}).get("failed") == 1 for e in audit)
