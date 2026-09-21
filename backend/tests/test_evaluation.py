"""Self-evaluation: bundle-only submission, organiser scoring by path, per-email diffs, API + report."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault("REPO_BACKEND", "memory")
os.environ["AUTO_SEED"] = "0"
os.environ["LLM_PROVIDER"] = "none"

from app.config import get_repo  # noqa: E402
from app.main import app  # noqa: E402
from app.services import evaluation as ev  # noqa: E402

client = TestClient(app)
SUP = {"X-User-Id": "u_sup_1"}
SI = "SHIPPING INSTRUCTION\nShipper: APRIL FAR EAST (M) SDN BHD\nConsignee: MOORIM SP CO., LTD\nNotify Party: UAB NOVAKOPA\nPort of Loading: PORT KLANG (WESTPORT), MALAYSIA (MYPKG)\nPort of Discharge: CALLAO, PERU (PECLL)\nContainer Count: 3 x 40'HC\nGross Weight (KG): 22,000 KG\n"
BL_BAD = SI.replace("SHIPPING INSTRUCTION", "BILL OF LADING (DRAFT)").replace("3 x 40'HC", "4 x 40'HC")
BL_OK = SI.replace("SHIPPING INSTRUCTION", "BILL OF LADING (DRAFT)")


def _ingest(email_id: str, bl: str | None = BL_BAD, *, subject: str | None = None, body: str | None = None, sender: str = "docs@vitalsolutions.sg") -> None:
    atts = [{"name": f"{email_id}_SI.txt", "content_base64": base64.b64encode(SI.encode()).decode()}]
    if bl is not None:
        atts.append({"name": f"{email_id}_BL.txt", "content_base64": base64.b64encode(bl.encode()).decode()})
    payload = {"email_id": email_id, "from": sender, "subject": subject or f"REQUEST BL DRAFT _ {email_id}",
               "body": body or "Attached are the SI and draft BL. Please check the details and confirm.", "attachments": atts if bl is not None or subject is None else []}
    if subject and bl is None and body:
        payload["attachments"] = []
    r = client.post("/webhooks/email", json=payload, headers=SUP)
    assert r.status_code == 201, r.text


def _fixture(tmp_path: Path, truth: dict) -> tuple[Path, Path]:
    bundle = tmp_path / "bundle"
    (bundle / "inbox").mkdir(parents=True)
    for eid in truth:
        (bundle / "inbox" / f"{eid}.json").write_text("{}", encoding="utf-8")
    gt = tmp_path / "ground_truth.json"
    gt.write_text(json.dumps(truth), encoding="utf-8")
    return bundle, gt


TRUTH = {
    "ev_001": {"category": "BL_COMPARISON", "status": "MISMATCH", "review_reason": None, "defect_fields": ["container_count"], "has_defect": True},
    "ev_002": {"category": "BL_COMPARISON", "status": "OK", "review_reason": None, "defect_fields": [], "has_defect": False},
    "ev_003": {"category": "SPAM", "status": "OK", "review_reason": None, "defect_fields": [], "has_defect": False},
    "ev_004": {"category": "INVOICE_QUERY", "status": "OK", "review_reason": None, "defect_fields": [], "has_defect": False},
}


def test_submission_is_bundle_only_and_reports_missing(tmp_path):
    _ingest("ev_001")
    _ingest("ev_002", bl=BL_OK)
    _ingest("ev_003", bl=None, subject="Increase your shipping revenue with this ONE weird trick", body="Dear user, your mailbox has exceeded its storage limit. Verify your account within 24 hours to avoid deactivation: http://webmail-verify.co", sender="info@crypto-invest.net")
    _ingest("not_in_bundle_1", bl=BL_OK)
    bundle, gt = _fixture(tmp_path, TRUTH)
    ids = ev.bundle_ids(bundle)
    assert ids == ["ev_001", "ev_002", "ev_003", "ev_004"]
    submission, missing = ev.build_submission(get_repo(), ids)
    assert set(submission) == set(ids) and "not_in_bundle_1" not in submission
    assert missing == ["ev_004"] and submission["ev_004"] == ev.PLACEHOLDER
    assert submission["ev_001"]["status"] == "MISMATCH" and submission["ev_001"]["defect_fields"] == ["container_count"] and submission["ev_001"]["has_defect"] is True
    assert submission["ev_002"]["status"] == "OK" and submission["ev_003"]["category"] == "SPAM"
    assert set(submission["ev_001"]) == set(ev.KEYS), "exactly the sample_submission shape"

    result = ev.evaluate(get_repo(), truth_path=gt, bundle_dir=bundle)
    sb = result["scoreboard"]
    assert {"stage1", "stage3", "reliability", "end_to_end", "final_score"} <= set(sb)
    assert sb["end_to_end"]["success"] == 1 and sb["end_to_end"]["total"] == 1, "the mismatch case is fully right"
    assert result["counts"] == {"bundle_emails": 4, "reference_emails": 4, "answered": 3, "missing": 1}
    d = result["diffs"]
    assert [r["email_id"] for r in d["category"]] == ["ev_004"], "the missing email counts as GENERAL -> category error"
    assert d["mismatch_flag"] == [] and d["defect_fields"] == [] and d["escalation"] == []


def test_diff_finds_false_alarms_missed_fields_and_escalation_errors():
    truth = {
        "a": {"category": "BL_COMPARISON", "status": "OK", "review_reason": None, "defect_fields": [], "has_defect": False},
        "b": {"category": "BL_COMPARISON", "status": "MISMATCH", "review_reason": None, "defect_fields": ["shipper", "consignee"], "has_defect": True},
        "c": {"category": "BL_COMPARISON", "status": "NEEDS_REVIEW", "review_reason": "unreadable", "defect_fields": [], "has_defect": False},
        "d": {"category": "GENERAL", "status": "OK", "review_reason": None, "defect_fields": [], "has_defect": False},
    }
    sub = {
        "a": {"category": "BL_COMPARISON", "status": "MISMATCH", "review_reason": None, "defect_fields": ["gross_weight_kg"], "has_defect": True},   # false alarm
        "b": {"category": "BL_COMPARISON", "status": "MISMATCH", "review_reason": None, "defect_fields": ["shipper"], "has_defect": True},           # missing a field
        "c": {"category": "BL_COMPARISON", "status": "OK", "review_reason": None, "defect_fields": [], "has_defect": False},                          # should have escalated
        "d": {"category": "SPAM", "status": "OK", "review_reason": None, "defect_fields": [], "has_defect": False},                                   # misclassified
    }
    d = ev.diff(sub, truth)
    assert [r["email_id"] for r in d["category"]] == ["d"]
    assert {r["email_id"] for r in d["status"]} == {"a", "c"}
    assert d["mismatch_flag"] == [{"email_id": "a", "kind": "false alarm", "desk_fields": ["gross_weight_kg"], "truth_fields": [], "link": "/cases/case_a"}]
    assert d["defect_fields"][0]["email_id"] == "b" and d["defect_fields"][0]["missing"] == ["consignee"] and d["defect_fields"][0]["extra"] == []
    assert d["escalation"][0]["email_id"] == "c" and d["escalation"][0]["kind"].startswith("decided but")
    md = ev.render_report_md({"generated_at": "t", "counts": {"bundle_emails": 4, "reference_emails": 4, "answered": 4, "missing": 0}, "missing": [],
                              "scoreboard": ev.score_locally(sub, truth), "diffs": d, "submission": sub})
    assert "FINAL SCORE" in md and "false alarm" in md and "| d | SPAM | GENERAL |" in md


def test_evaluate_api_and_report(monkeypatch, tmp_path):
    _ingest("ev_101")
    bundle, gt = _fixture(tmp_path, {"ev_101": TRUTH["ev_001"]})
    monkeypatch.setattr(ev, "GROUND_TRUTH", gt)
    monkeypatch.setattr(ev, "BUNDLE_DIR", bundle)
    r = client.get("/evaluate", headers=SUP)
    assert r.status_code == 200, r.text
    body = r.json()
    # one-email reference: accuracy and end-to-end are perfect; macro-F1 is diluted by the four absent categories, so final_score < 1 by design
    assert body["scoreboard"]["stage1"]["accuracy"] == 1.0 and body["scoreboard"]["end_to_end"]["rate"] == 1.0 and body["counts"]["answered"] == 1 and "submission" not in body
    assert all(body["diffs"][k] == [] for k in body["diffs"])
    md = client.get("/evaluate/report.md", headers=SUP)
    assert md.status_code == 200 and md.text.startswith("# NovaShip Averis") and "novaship-evaluation.md" in md.headers["content-disposition"]
    sub = client.get("/evaluate/submission.json", headers=SUP)
    assert sub.status_code == 200 and list(sub.json()) == ["ev_101"] and sub.headers["x-missing"] == "0"
    assert client.get("/evaluate", headers={"X-User-Id": "u_ops_1"}).status_code == 403, "export_data permission required"
    audit = client.get("/audit", params={"action": "SELF_EVALUATION"}, headers=SUP).json()["events"]
    assert audit and audit[0]["after"]["answered"] == 1 and 0 < audit[0]["after"]["final_score"] <= 1

    monkeypatch.setattr(ev, "GROUND_TRUTH", tmp_path / "nope.json")
    assert client.get("/evaluate", headers=SUP).status_code == 404


def test_uploaded_reference_works_without_the_private_file(monkeypatch, tmp_path):
    """Deployed instances have no ground_truth.json: the card uploads one, it is scored in memory, the report comes back inline."""
    _ingest("ev_201")
    bundle, _gt = _fixture(tmp_path, {"ev_201": TRUTH["ev_001"]})
    monkeypatch.setattr(ev, "GROUND_TRUTH", tmp_path / "absent.json")
    monkeypatch.setattr(ev, "BUNDLE_DIR", bundle)
    assert client.get("/evaluate", headers=SUP).status_code == 404
    truth = json.dumps({"ev_201": TRUTH["ev_001"]}).encode()
    r = client.post("/evaluate", files={"ground_truth": ("ground_truth.json", truth, "application/json")}, data={"server": ""}, headers=SUP)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reference"] == "uploaded" and body["scoreboard"]["end_to_end"]["rate"] == 1.0 and body["report_md"].startswith("# NovaShip Averis")
    assert all(body["diffs"][k] == [] for k in body["diffs"])
    bad = client.post("/evaluate", files={"ground_truth": ("x.json", b"[1,2]", "application/json")}, headers=SUP)
    assert bad.status_code == 400 and "not valid" in bad.json()["detail"]["error"]
    # no bundle folder at all (e.g. Vercel): the uploaded reference defines the email set
    monkeypatch.setattr(ev, "BUNDLE_DIR", tmp_path / "no-bundle")
    r2 = client.post("/evaluate", files={"ground_truth": ("ground_truth.json", truth, "application/json")}, headers=SUP).json()
    assert r2["counts"]["bundle_emails"] == 1 and r2["counts"]["answered"] == 1


def test_vendored_scorer_matches_the_organiser_file_and_serves_deployments_without_it(monkeypatch, tmp_path):
    """backend/app/services/sdoc_scoring.py must stay byte-identical (below the marker) to the organiser's scoring.py,
    and the evaluation must work when the repo folders are not shipped (Vercel bundles only backend/)."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    original = (root / "sdoc-hackathon-docker" / "server" / "scoring.py").read_text(encoding="utf-8")
    vendored = (root / "backend" / "app" / "services" / "sdoc_scoring.py").read_text(encoding="utf-8")
    assert vendored.split("# --- ORIGINAL BELOW", 1)[1].split("\n", 1)[1] == original, "re-copy scoring.py into sdoc_scoring.py"

    _ingest("ev_301")
    monkeypatch.setattr(ev, "SCORING", tmp_path / "no-scoring.py")
    monkeypatch.setattr(ev, "GROUND_TRUTH", tmp_path / "no-truth.json")
    monkeypatch.setattr(ev, "BUNDLE_DIR", tmp_path / "no-bundle")
    assert ev.scoring_available() is True
    h = client.get("/health").json()
    assert h["runtime"] in ("server", "vercel") and h["evaluation"] == {"reference_on_server": False, "scorer": "vendored"}
    r = client.get("/evaluate", headers=SUP)
    assert r.status_code == 404 and r.json()["detail"]["code"] == "REFERENCE_NOT_ON_SERVER"
    truth = json.dumps({"ev_301": TRUTH["ev_001"]}).encode()
    up = client.post("/evaluate", files={"ground_truth": ("ground_truth.json", truth, "application/json")}, headers=SUP)
    assert up.status_code == 200, up.text
    assert up.json()["scoreboard"]["end_to_end"]["rate"] == 1.0 and up.json()["counts"]["bundle_emails"] == 1
