"""
Self-evaluation of the desk's *existing* results against the hackathon reference.

No pipeline replay: the submission is built from the cases the repository already holds
(`case_to_submission_row`, the same mapping as GET /export/submission.json), restricted to the
bundle's email ids, then scored with the organiser's own `scoring.py` (imported by path) against
`ground_truth.json`, or POSTed to the organiser server (`POST /submit`) when one is running.

Everything here is pure: the API route, the CLI (backend/scripts/evaluate.py) and tests share it.
"""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from app.services.submission import case_to_submission_row

import os

ROOT = Path(__file__).resolve().parents[3]
BUNDLE_DIR = Path(os.environ.get("BUNDLE_DIR") or ROOT / "sdoc-hackathon-bundle")
GROUND_TRUTH = Path(os.environ.get("EVAL_GROUND_TRUTH") or ROOT / "sdoc-hackathon-docker" / "data_v2" / "ground_truth.json")
SCORING = Path(os.environ.get("EVAL_SCORING") or ROOT / "sdoc-hackathon-docker" / "server" / "scoring.py")
PLACEHOLDER = {"category": "GENERAL", "status": "OK", "review_reason": None, "defect_fields": [], "has_defect": False}
KEYS = ("category", "status", "review_reason", "defect_fields", "has_defect")


def bundle_ids(bundle_dir: Optional[Path] = None) -> list[str]:
    """Every email id in the bundle inbox, in file order (the reference covers exactly these)."""
    inbox = Path(bundle_dir or BUNDLE_DIR) / "inbox"
    if not inbox.exists():
        return []
    return [p.stem for p in sorted(inbox.glob("*.json"))]


def build_submission(repo, ids: list[str]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """The desk's current answer for each bundle id (placeholder + `missing` list for ids the desk never saw)."""
    by_email: dict[str, dict[str, Any]] = {}
    for c in repo.list_cases():
        e = repo.get_email(c.source_email_id)
        if e is None:
            continue
        row = case_to_submission_row(c, e).model_dump(mode="json")
        by_email[e.id] = {k: row.get(k) for k in KEYS}
    out: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for eid in ids:
        if eid in by_email:
            out[eid] = by_email[eid]
        else:
            out[eid] = dict(PLACEHOLDER)
            missing.append(eid)
    return out, missing


def load_scoring(path: Path = SCORING):
    spec = importlib.util.spec_from_file_location("sdoc_scoring", str(path))
    if spec is None or spec.loader is None:
        raise FileNotFoundError(f"scoring module not found at {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def score_locally(submission: dict[str, Any], truth: dict[str, Any], scoring_path: Path = SCORING) -> dict[str, Any]:
    """The organiser's scoreboard (stage1 / stage3 / reliability / end_to_end / final_score), computed offline."""
    return load_scoring(scoring_path).score_all(truth, submission)


def submit_to_server(submission: dict[str, Any], base_url: str, timeout: float = 60.0) -> dict[str, Any]:
    """POST /submit on the organiser server; returns its scoreboard JSON."""
    import httpx

    r = httpx.post(base_url.rstrip("/") + "/submit", json=submission, timeout=timeout)
    r.raise_for_status()
    return r.json()


def diff(submission: dict[str, Any], truth: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Per-email disagreements, grouped the way the scorer penalises them. Each row links to the case."""
    out: dict[str, list[dict[str, Any]]] = {"category": [], "status": [], "mismatch_flag": [], "defect_fields": [], "escalation": []}
    for eid, gold in truth.items():
        pred = submission.get(eid) or PLACEHOLDER
        link = f"/cases/case_{eid}"
        if pred.get("category") != gold.get("category"):
            out["category"].append({"email_id": eid, "desk": pred.get("category"), "truth": gold.get("category"), "link": link})
        if pred.get("status") != gold.get("status") or (pred.get("review_reason") or None) != (gold.get("review_reason") or None):
            out["status"].append({"email_id": eid, "desk": pred.get("status"), "desk_reason": pred.get("review_reason"), "truth": gold.get("status"),
                                  "truth_reason": gold.get("review_reason"), "link": link})
        if gold.get("category") == "BL_COMPARISON" and gold.get("status") != "NEEDS_REVIEW":
            gp, gt = bool(pred.get("has_defect")), bool(gold.get("has_defect"))
            if gp != gt:
                out["mismatch_flag"].append({"email_id": eid, "kind": "false alarm" if gp else "missed mismatch", "desk_fields": pred.get("defect_fields") or [],
                                             "truth_fields": gold.get("defect_fields") or [], "link": link})
            elif gt:
                pf, tf = set(pred.get("defect_fields") or []), set(gold.get("defect_fields") or [])
                if pf != tf:
                    out["defect_fields"].append({"email_id": eid, "extra": sorted(pf - tf), "missing": sorted(tf - pf), "desk_fields": sorted(pf), "truth_fields": sorted(tf), "link": link})
        if gold.get("category") == "BL_COMPARISON" and (gold.get("status") == "NEEDS_REVIEW") != (pred.get("status") == "NEEDS_REVIEW"):
            out["escalation"].append({"email_id": eid, "kind": "escalated but reference says decide" if pred.get("status") == "NEEDS_REVIEW" else "decided but reference says escalate",
                                      "desk": pred.get("status"), "desk_reason": pred.get("review_reason"), "truth": gold.get("status"), "truth_reason": gold.get("review_reason"), "link": link})
    return out


def validate_truth(data: Any) -> dict[str, Any]:
    """An uploaded reference must look like ground_truth.json: {email_id: {category, status, ...}}."""
    if not isinstance(data, dict) or not data:
        raise ValueError("ground truth must be a non-empty JSON object keyed by email_id")
    bad = [k for k, v in list(data.items())[:50] if not isinstance(v, dict) or "category" not in v or "status" not in v]
    if bad:
        raise ValueError(f"ground truth rows need 'category' and 'status' (first bad key: {bad[0]})")
    return data


def evaluate(repo, *, truth_path: Optional[Path] = None, scoring_path: Optional[Path] = None, bundle_dir: Optional[Path] = None,
             server: Optional[str] = None, submission: Optional[dict[str, Any]] = None, truth: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Build (or take) the submission, score it locally and optionally on the organiser server, and diff it.
    Paths default to the module constants at call time (tests and deployments can point them elsewhere);
    `truth` (an uploaded reference) bypasses the file so a deployment without the private file can still evaluate."""
    truth_path = truth_path or GROUND_TRUTH
    scoring_path = scoring_path or SCORING
    bundle_dir = bundle_dir or BUNDLE_DIR
    ids = bundle_ids(bundle_dir)
    if truth is not None and not ids:
        ids = list(truth)   # no bundle folder on this deployment: the reference defines the email set
    if submission is None:
        submission, missing = build_submission(repo, ids)
    else:
        missing = [i for i in ids if i not in submission]
        submission = {i: submission.get(i, dict(PLACEHOLDER)) for i in ids} if ids else dict(submission)
    uploaded = truth is not None
    truth = validate_truth(truth) if uploaded else json.loads(Path(truth_path).read_text(encoding="utf-8"))
    scoreboard = score_locally(submission, truth, scoring_path)
    result: dict[str, Any] = {
        "generated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "counts": {"bundle_emails": len(ids), "reference_emails": len(truth), "answered": len(ids) - len(missing), "missing": len(missing)},
        "missing": missing, "scoreboard": scoreboard, "diffs": diff(submission, truth), "submission": submission,
        "reference": "uploaded" if uploaded else "server file",
    }
    if server:
        try:
            result["server"] = {"url": server, "scoreboard": submit_to_server(submission, server)}
        except Exception as exc:
            result["server"] = {"url": server, "error": f"{type(exc).__name__}: {exc}"}
    return result


def render_report_md(result: dict[str, Any]) -> str:
    """Markdown scoreboard in the layout of the organiser's score_cli.py, plus the per-email diff tables."""
    s = result["scoreboard"]
    s1, s3, rel, e2e = s.get("stage1", {}), s.get("stage3", {}), s.get("reliability", {}), s.get("end_to_end", {})
    c = result["counts"]

    def pct(x: Any) -> str:
        try:
            return f"{float(x):.3f}"
        except (TypeError, ValueError):
            return "-"

    lines = [
        "# NovaShip Averis — self-evaluation of current desk results", "",
        f"Generated {result['generated_at']} · {c['answered']}/{c['bundle_emails']} bundle emails answered by the desk"
        + (f" · **{c['missing']} missing** (scored as GENERAL/OK)" if c["missing"] else "") + " · scored offline with the organiser's `scoring.py`.", "",
        f"## FINAL SCORE {pct(s.get('final_score'))}", "",
        "| Stage | Metric | Value |", "|---|---|---|",
        f"| Classification | accuracy | {pct(s1.get('accuracy'))} |", f"| Classification | macro-F1 | {pct(s1.get('macro_f1'))} |",
        f"| Mismatch detection | defect precision | {pct(s3.get('defect_precision'))} |", f"| Mismatch detection | defect recall | {pct(s3.get('defect_recall'))} |",
        f"| Mismatch detection | field-level F1 | {pct(s3.get('field_f1'))} |", f"| Mismatch detection | exact match rate | {pct(s3.get('exact_match_rate'))} |",
        f"| Reliability | escalation recall | {pct(rel.get('escalation_recall'))} |", f"| Reliability | escalation precision | {pct(rel.get('escalation_precision'))} |",
        f"| End to end | mismatch cases fully right | {e2e.get('success', '-')}/{e2e.get('total', '-')} = {pct(e2e.get('rate'))} |", "",
        "## Per-category precision / recall / F1", "", "| Category | TP | FP | FN | P | R | F1 |", "|---|---|---|---|---|---|---|",
    ]
    for cat, v in (s1.get("per") or {}).items():
        tp, fp, fn = v.get("tp", 0), v.get("fp", 0), v.get("fn", 0)
        p = tp / (tp + fp) if tp + fp else 0.0
        r = tp / (tp + fn) if tp + fn else 0.0
        f = 2 * p * r / (p + r) if p + r else 0.0
        lines.append(f"| {cat} | {tp} | {fp} | {fn} | {p:.3f} | {r:.3f} | {f:.3f} |")
    d = result["diffs"]
    lines += ["", "## Where the desk disagrees with the reference", ""]
    titles = {"category": "Classification errors", "status": "Status / review-reason errors", "mismatch_flag": "Mismatch flag errors (false alarms, misses)",
              "defect_fields": "Defect-field errors on correctly flagged mismatches", "escalation": "Escalation errors (asked for help at the wrong time)"}
    for key, title in titles.items():
        rows = d.get(key) or []
        lines.append(f"### {title} — {len(rows)}")
        if not rows:
            lines += ["", "None.", ""]
            continue
        lines += ["", "| Email | Desk | Reference | Note |", "|---|---|---|---|"]
        for r in rows:
            if key == "category":
                lines.append(f"| {r['email_id']} | {r['desk']} | {r['truth']} | |")
            elif key == "status":
                lines.append(f"| {r['email_id']} | {r['desk']} ({r.get('desk_reason') or '-'}) | {r['truth']} ({r.get('truth_reason') or '-'}) | |")
            elif key == "mismatch_flag":
                lines.append(f"| {r['email_id']} | {', '.join(r['desk_fields']) or 'no defect'} | {', '.join(r['truth_fields']) or 'no defect'} | {r['kind']} |")
            elif key == "defect_fields":
                lines.append(f"| {r['email_id']} | {', '.join(r['desk_fields'])} | {', '.join(r['truth_fields'])} | extra: {', '.join(r['extra']) or '-'}; missing: {', '.join(r['missing']) or '-'} |")
            else:
                lines.append(f"| {r['email_id']} | {r['desk']} ({r.get('desk_reason') or '-'}) | {r['truth']} ({r.get('truth_reason') or '-'}) | {r['kind']} |")
        lines.append("")
    if result.get("server"):
        srv = result["server"]
        lines += ["## Organiser server", "", f"`{srv['url']}` → " + (f"final score {pct(srv['scoreboard'].get('final_score'))}" if "scoreboard" in srv else f"error: {srv['error']}"), ""]
    if result.get("missing"):
        lines += ["## Bundle emails the desk has no case for", "", ", ".join(result["missing"]), ""]
    lines += ["---", "The scoreboard is a development aid (per the brief): a score cannot tell whether the desk asked for human review at the right moment or gave enough context. When the desk disagrees with the reference, check the source documents before changing anything; if the desk's decision is reasonable, record the reason in the case.", ""]
    return "\n".join(lines)
