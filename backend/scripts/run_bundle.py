#!/usr/bin/env python3
"""
Run the full pipeline over the SDOC bundle, write submission.json, and (if the
organiser ground truth is available) print the official scoreboard.

    cd backend
    python scripts/run_bundle.py                       # uses ../sdoc-hackathon-bundle
    python scripts/run_bundle.py --bundle /path --out submission.json
    python scripts/run_bundle.py --snapshot ../supabase/seed/snapshot.json   # also dump repo snapshot

Person 1 regression tool: run after any change to extractor/normalizer/comparator.
"""
from __future__ import annotations

import argparse
import os
import collections
import importlib.util
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

from app.pipeline.orchestrator import Pipeline  # noqa: E402
from app.repositories.memory import MemoryRepository  # noqa: E402
from app.services.submission import case_to_submission_row  # noqa: E402


def load_scoring(path: Path):
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location("sdoc_scoring", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", default=str(ROOT / "sdoc-hackathon-bundle"))
    ap.add_argument("--out", default=str(ROOT / "submission.json"))
    ap.add_argument("--ground-truth", default=str(ROOT / "sdoc-hackathon-docker" / "data_v2" / "ground_truth.json"))
    ap.add_argument("--scoring", default=str(ROOT / "sdoc-hackathon-docker" / "server" / "scoring.py"))
    ap.add_argument("--snapshot", default=None, help="Also write a repository snapshot JSON (seed for the API / Supabase)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--ocr", action="store_true", help="Allow Gemini/pytesseract OCR during the replay (off by default so scoring stays deterministic and offline)")
    args = ap.parse_args()
    if not args.ocr:
        os.environ["OCR_ENABLED"] = "0"

    bundle = Path(args.bundle)
    repo = MemoryRepository()
    pipe = Pipeline(repo)
    submission: dict[str, dict] = {}
    statuses = collections.Counter()
    t0 = time.time()
    files = sorted((bundle / "inbox").glob("email_*.json"))
    if args.limit:
        files = files[: args.limit]
    for p in files:
        raw = json.loads(p.read_text(encoding="utf-8"))
        blobs = {a: (bundle / a).read_bytes() for a in raw.get("attachments", []) if (bundle / a).exists()}
        email = pipe.ingest_email(raw, blobs)
        case = pipe.run(email)
        statuses[case.status.value] += 1
        submission[email.id] = case_to_submission_row(case, email).model_dump(mode="json")
    elapsed = time.time() - t0
    Path(args.out).write_text(json.dumps(submission, indent=2), encoding="utf-8")
    print(f"processed {len(files)} emails in {elapsed:.1f}s ({1000*elapsed/max(1,len(files)):.0f} ms/email) -> {args.out}")
    print("case statuses:", dict(statuses))

    if args.snapshot:
        repo.save_file(args.snapshot)
        print(f"snapshot written -> {args.snapshot}")

    gt_path = Path(args.ground_truth)
    scoring = load_scoring(Path(args.scoring))
    if gt_path.exists() and scoring:
        gt = json.loads(gt_path.read_text(encoding="utf-8"))
        res = scoring.score_all(gt, submission)
        s1, s3, rel, e2e = res["stage1"], res["stage3"], res["reliability"], res["end_to_end"]
        print("\n=== OFFICIAL SCOREBOARD ===")
        print(f"stage1  accuracy={s1['accuracy']:.3f}  macro_f1={s1['macro_f1']:.3f}")
        print(f"stage3  defect_f1={s3['defect_f1']:.3f}  field_f1={s3['field_f1']:.3f}  exact_match_rate={s3['exact_match_rate']:.3f}")
        print(f"e2e     {e2e['success']}/{e2e['total']} = {e2e['rate']:.3f}")
        print(f"reliab. escalation_recall={rel['escalation_recall']:.3f} precision={rel['escalation_precision']:.3f} f1={rel['escalation_f1']:.3f}")
        print(f"FINAL SCORE = {res['final_score']:.4f}")
        wrong = [(e, gt[e]["category"], submission[e]["category"]) for e in gt if gt[e]["category"] != submission[e]["category"]]
        if wrong:
            print("misclassified:", wrong[:20])
        diffs = [(e, gt[e]["status"], submission[e]["status"], gt[e]["review_reason"], submission[e]["review_reason"]) for e in gt
                 if gt[e]["status"] != submission[e]["status"] or gt[e]["review_reason"] != submission[e]["review_reason"]]
        if diffs:
            print(f"status/review diffs ({len(diffs)}):", diffs[:20])
        return 0 if res["final_score"] >= 0.99 else 1
    print("(no ground truth available - submission written only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
