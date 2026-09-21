#!/usr/bin/env python3
"""
Score the desk's *current* results against the hackathon reference (no pipeline replay).

    python backend/scripts/evaluate.py                                  # results from the running API, local ground truth
    python backend/scripts/evaluate.py --server http://localhost:8081   # also POST /submit to the organiser server
    python backend/scripts/evaluate.py --file submission.json           # score an existing submission file instead
    python backend/scripts/evaluate.py --fail-below 0.95                # non-zero exit when FINAL SCORE is lower (CI gate)

Writes evaluation/submission.json, evaluation/scoreboard.json and evaluation/report.md.
The reference (sdoc-hackathon-docker/data_v2/ground_truth.json) and the organiser's scoring.py
must be present locally; both ship with the docker bundle.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.services import evaluation as ev  # noqa: E402


class _ApiRepo:
    """Just enough of the repository protocol for build_submission(): rows come from GET /export/submission.json."""

    def __init__(self, rows: dict[str, dict]) -> None:
        self.rows = rows

    def list_cases(self):
        return []


def fetch_submission(api: str, token: str | None, user: str) -> dict[str, dict]:
    headers = {"Authorization": f"Bearer {token}"} if token else {"X-User-Id": user}
    req = urllib.request.Request(api.rstrip("/") + "/export/submission.json", headers=headers)
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", default="http://localhost:8000", help="running NovaShip API to read results from")
    ap.add_argument("--token", default=None, help="session token (default: demo header X-User-Id)")
    ap.add_argument("--user", default="u_admin_1", help="X-User-Id for demo mode")
    ap.add_argument("--file", default=None, help="score this submission.json instead of the API's results")
    ap.add_argument("--server", default=None, help="organiser server base URL to POST /submit to as well")
    ap.add_argument("--ground-truth", default=str(ev.GROUND_TRUTH))
    ap.add_argument("--scoring", default=str(ev.SCORING))
    ap.add_argument("--bundle", default=str(ev.BUNDLE_DIR))
    ap.add_argument("--out", default=str(ROOT / "evaluation"))
    ap.add_argument("--fail-below", type=float, default=None, help="exit 1 when final_score is below this")
    a = ap.parse_args(argv)

    truth_path, scoring_path = Path(a.ground_truth), Path(a.scoring)
    if not truth_path.exists():
        sys.exit(f"ground truth not found at {truth_path} (it ships with sdoc-hackathon-docker/data_v2)")
    if not scoring_path.exists():
        sys.exit(f"scoring.py not found at {scoring_path}")

    if a.file:
        submission = json.loads(Path(a.file).read_text(encoding="utf-8"))
        source = f"file {a.file}"
    else:
        try:
            submission = fetch_submission(a.api, a.token, a.user)
        except Exception as exc:
            sys.exit(f"could not read {a.api}/export/submission.json ({type(exc).__name__}: {exc}); is the API running?")
        source = f"desk results from {a.api}"
    result = ev.evaluate(_ApiRepo({}), truth_path=truth_path, scoring_path=scoring_path, bundle_dir=Path(a.bundle), server=a.server, submission=submission)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "submission.json").write_text(json.dumps(result["submission"], indent=2), encoding="utf-8")
    (out / "scoreboard.json").write_text(json.dumps({k: v for k, v in result.items() if k != "submission"}, indent=2), encoding="utf-8")
    (out / "report.md").write_text(ev.render_report_md(result), encoding="utf-8")

    s = result["scoreboard"]
    s1, s3, rel, e2e, c = s.get("stage1", {}), s.get("stage3", {}), s.get("reliability", {}), s.get("end_to_end", {}), result["counts"]

    def bar(x: float, width: int = 24) -> str:
        n = int(round(float(x or 0) * width))
        return "#" * n + "." * (width - n)

    print(f"source: {source}")
    print(f"answered {c['answered']}/{c['bundle_emails']} bundle emails" + (f"  (missing: {c['missing']})" if c["missing"] else ""))
    print(f"\n  FINAL SCORE       {float(s.get('final_score', 0)):.4f}  {bar(s.get('final_score', 0))}")
    print(f"  accuracy          {float(s1.get('accuracy', 0)):.3f}  {bar(s1.get('accuracy', 0))}")
    print(f"  macro-F1          {float(s1.get('macro_f1', 0)):.3f}  {bar(s1.get('macro_f1', 0))}")
    print(f"  defect recall     {float(s3.get('defect_recall', 0)):.3f}  {bar(s3.get('defect_recall', 0))}")
    print(f"  defect precision  {float(s3.get('defect_precision', 0)):.3f}  {bar(s3.get('defect_precision', 0))}")
    print(f"  field-level F1    {float(s3.get('field_f1', 0)):.3f}  {bar(s3.get('field_f1', 0))}")
    print(f"  escalation recall {float(rel.get('escalation_recall', 0)):.3f}  {bar(rel.get('escalation_recall', 0))}")
    print(f"  escalation prec.  {float(rel.get('escalation_precision', 0)):.3f}  {bar(rel.get('escalation_precision', 0))}")
    print(f"  end-to-end        {e2e.get('success', 0)}/{e2e.get('total', 0)} = {float(e2e.get('rate', 0)):.3f}")
    d = result["diffs"]
    print(f"\n  disagreements: category {len(d['category'])} | status {len(d['status'])} | mismatch flag {len(d['mismatch_flag'])} | defect fields {len(d['defect_fields'])} | escalation {len(d['escalation'])}")
    for key in ("category", "status", "mismatch_flag"):
        for r in d[key][:10]:
            print(f"    {key:13s} {r['email_id']}: desk={r.get('desk') or r.get('desk_fields')} reference={r.get('truth') or r.get('truth_fields')} {r.get('kind', '')}")
    if result.get("server"):
        srv = result["server"]
        print(f"\n  organiser server {srv['url']}: " + (f"final score {float(srv['scoreboard'].get('final_score', 0)):.4f}" if "scoreboard" in srv else srv["error"]))
    print(f"\nwritten: {out / 'report.md'}, {out / 'scoreboard.json'}, {out / 'submission.json'}")
    if a.fail_below is not None and float(s.get("final_score", 0)) < a.fail_below:
        print(f"FINAL SCORE below {a.fail_below}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
