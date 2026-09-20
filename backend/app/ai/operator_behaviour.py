"""Evidence-based operator warnings and the N-action auto-draft rule.

Document anomalies stay in anomaly.py. This module watches the logged-in user.
Warnings never lock an account or send email.

Thresholds come from the `operator_guard` policy section and, when `adaptive`
is on, from the user's own history in the audit log (Supabase `audit_events`
or the memory repository): the burst limit tightens to a multiple of the user's
usual actions-per-minute, and a mutation far outside the user's usual working
hours is flagged. Everything is computed from audit events, so it survives
restarts and works across API instances.
"""
from __future__ import annotations

import math
import statistics
import time
from datetime import datetime, timedelta
from typing import Any, Optional

from app.contracts.schemas import ActorType, DraftStatus, SecuritySignal

MUTATION_ACTIONS = {
    "DRAFT_EDITED",
    "DRAFT_REJECTED",
    "RETRY_REQUESTED",
    "REVIEW_REQUESTED",
    "ASSIGNED",
    "COMPLETED",
    "MARKED_NO_ACTION",
}

# Defaults mirror DEFAULT_POLICY["operator_guard"]; kept here so callers without a policy still behave.
DEFAULT_GUARD: dict[str, Any] = {
    "auto_draft_after": 3,
    "burst_limit": 10,
    "burst_window_s": 60,
    "adaptive": True,
    "baseline_days": 30,
    "baseline_multiplier": 3.0,
    "min_baseline_events": 20,
    "min_effective_burst": 3,
    "off_hours_warning": True,
    "rapid_archive_limit": 5,
    "login_fail_limit": 3,
    "login_fail_window_s": 900,
    "warning_dialog": True,
}

# Backwards-compatible module constants (tests and older callers read these).
AUTO_DRAFT_THRESHOLD = DEFAULT_GUARD["auto_draft_after"]
BURST_WINDOW_S = DEFAULT_GUARD["burst_window_s"]
BURST_LIMIT = DEFAULT_GUARD["burst_limit"]
LOGIN_FAIL_WINDOW_S = DEFAULT_GUARD["login_fail_window_s"]
LOGIN_FAIL_LIMIT = DEFAULT_GUARD["login_fail_limit"]


def guard_settings(policy: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Effective operator-guard settings: policy section over defaults, with types coerced."""
    merged = dict(DEFAULT_GUARD)
    section = (policy or {}).get("operator_guard") if isinstance(policy, dict) else None
    for key, value in (section or {}).items():
        if key in merged and value is not None:
            merged[key] = value
    for key in ("auto_draft_after", "burst_limit", "burst_window_s", "baseline_days", "min_baseline_events", "min_effective_burst", "rapid_archive_limit", "login_fail_limit", "login_fail_window_s"):
        merged[key] = max(1, int(merged[key]))
    merged["baseline_multiplier"] = max(1.0, float(merged["baseline_multiplier"]))
    for key in ("adaptive", "off_hours_warning", "warning_dialog"):
        merged[key] = bool(merged[key])
    return merged


_BASELINE_TTL_S = 120
_baseline_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def clear_operator_burst() -> None:
    """Forget cached baselines (tests / after policy edits); burst state itself lives in the audit log."""
    _baseline_cache.clear()


def _actor_events(repo, user_id: str, since: datetime):
    lister = getattr(repo, "list_audit_for_actor", None)
    if callable(lister):
        events = lister(user_id, since)
    else:  # repositories without the dedicated query fall back to the recent global window
        events = [e for e in repo.list_audit(None) if e.actor_id == user_id and e.timestamp >= since]
    return [e for e in events if e.actor_type == ActorType.USER]


def count_user_mutations(repo, case_id: str) -> int:
    return sum(
        1
        for event in repo.list_audit(case_id)
        if event.actor_type == ActorType.USER and event.action in MUTATION_ACTIONS
    )


# ---------------------------------------------------------------- learned baseline
def operator_baseline(repo, user_id: str, settings: Optional[dict[str, Any]] = None, *, now: Optional[datetime] = None) -> dict[str, Any]:
    """What the guard has learned for one user from the audit log.

    Returns events counted, active minutes, median / p95 mutations per active minute, usual working
    hours (UTC, 5th-95th percentile) and the burst limit that applies right now.
    """
    s = settings or guard_settings()
    stamp = now or datetime.utcnow()
    cache_key = f"{user_id}|{s['baseline_days']}|{s['baseline_multiplier']}|{s['burst_limit']}|{s['adaptive']}"
    cached = _baseline_cache.get(cache_key)
    if cached and cached[0] > time.monotonic() and now is None:
        return dict(cached[1])
    since = stamp - timedelta(days=s["baseline_days"])
    mutations = [e for e in _actor_events(repo, user_id, since) if e.action in MUTATION_ACTIONS]
    per_minute: dict[str, int] = {}
    hours: list[int] = []
    for e in mutations:
        key = e.timestamp.strftime("%Y-%m-%dT%H:%M")
        per_minute[key] = per_minute.get(key, 0) + 1
        hours.append(e.timestamp.hour)
    counts = sorted(per_minute.values())
    median = statistics.median(counts) if counts else 0.0
    p95 = counts[min(len(counts) - 1, int(math.ceil(0.95 * len(counts))) - 1)] if counts else 0
    learned = s["adaptive"] and len(mutations) >= s["min_baseline_events"]
    effective = s["burst_limit"]
    if learned:
        effective = int(min(s["burst_limit"], max(s["min_effective_burst"], math.ceil(median * s["baseline_multiplier"]))))
    usual_hours: Optional[tuple[int, int]] = None
    if learned and hours:
        ordered = sorted(hours)
        lo = ordered[max(0, int(0.05 * len(ordered)))]
        hi = ordered[min(len(ordered) - 1, int(math.ceil(0.95 * len(ordered))) - 1)]
        usual_hours = (lo, hi)
    result = {
        "user_id": user_id,
        "baseline_days": s["baseline_days"],
        "events": len(mutations),
        "active_minutes": len(per_minute),
        "median_per_min": float(median),
        "p95_per_min": int(p95),
        "usual_hours_utc": list(usual_hours) if usual_hours else None,
        "learned": learned,
        "burst_window_s": s["burst_window_s"],
        "fixed_burst_limit": s["burst_limit"],
        "effective_burst_limit": effective,
        "auto_draft_after": s["auto_draft_after"],
    }
    if now is None:
        _baseline_cache[cache_key] = (time.monotonic() + _BASELINE_TTL_S, dict(result))
    return result


# ---------------------------------------------------------------- signals
def burst_warning(repo_or_user, user_id: Optional[str] = None, *, settings: Optional[dict[str, Any]] = None, now: Optional[datetime] = None,
                  limit: Optional[int] = None, window_s: Optional[int] = None, repo=None) -> Optional[SecuritySignal]:
    """Warn when the user's mutations inside the window reach the effective (learned or fixed) limit.

    Accepts `burst_warning(repo, user_id, ...)`; the legacy `burst_warning(user_id, repo=...)` form still works.
    Counts the audit log; callers audit the mutation before asking, so the current action is already included.
    """
    if isinstance(repo_or_user, str):
        user_id, repo = repo_or_user, repo
    else:
        repo = repo_or_user
    if repo is None or not user_id:
        return None
    s = guard_settings() if settings is None else settings
    window = s["burst_window_s"] if window_s is None else window_s
    stamp = now or datetime.utcnow()
    since = stamp - timedelta(seconds=window)
    recent = [e for e in _actor_events(repo, user_id, since) if e.action in MUTATION_ACTIONS]
    n = len(recent)
    baseline = operator_baseline(repo, user_id, s, now=now) if limit is None else None
    effective = limit if limit is not None else baseline["effective_burst_limit"]
    if n < effective:
        return None
    if baseline and baseline["learned"]:
        evidence = (f"{n} operator actions in {window}s by {user_id}; your usual pace is about "
                    f"{baseline['median_per_min']:.1f}/min (limit {effective} learned from {baseline['events']} audited actions).")
    else:
        evidence = f"{n} operator actions in {window}s by {user_id} (fixed limit {effective})."
    return SecuritySignal(
        signal="OPERATOR_BURST_MUTATIONS",
        severity="MEDIUM",
        evidence=evidence,
        recommended_action="Pause and confirm each action; this is a warning only.",
    )


def off_hours_warning(repo, user_id: str, settings: Optional[dict[str, Any]] = None, *, now: Optional[datetime] = None) -> Optional[SecuritySignal]:
    """LOW warning when a mutation happens well outside the hours the user normally works (learned from the audit log)."""
    s = settings or guard_settings()
    if not s["off_hours_warning"]:
        return None
    stamp = now or datetime.utcnow()
    baseline = operator_baseline(repo, user_id, s, now=now)
    usual = baseline.get("usual_hours_utc")
    if not usual:
        return None
    lo, hi = usual
    hour = stamp.hour
    inside = lo <= hour <= hi if lo <= hi else (hour >= lo or hour <= hi)
    if inside or abs(hour - lo) <= 1 or abs(hour - hi) <= 1:
        return None
    return SecuritySignal(
        signal="OPERATOR_OFF_HOURS",
        severity="LOW",
        evidence=f"Action at {stamp.strftime('%H:%M')} UTC; {user_id} usually works {lo:02d}:00-{hi:02d}:59 UTC (from {baseline['events']} audited actions).",
        recommended_action="No action needed if this is you. Report it if it is not.",
    )


def login_failed_warning(repo, email: str, *, now: Optional[datetime] = None, settings: Optional[dict[str, Any]] = None) -> Optional[SecuritySignal]:
    s = settings or guard_settings()
    stamp = now or datetime.utcnow()
    cutoff = stamp - timedelta(seconds=s["login_fail_window_s"])
    fails = [
        event
        for event in repo.list_audit(None)
        if event.action == "LOGIN_FAILED"
        and event.timestamp >= cutoff
        and (event.after or {}).get("email") == email
    ]
    if len(fails) < s["login_fail_limit"]:
        return None
    return SecuritySignal(
        signal="OPERATOR_REPEATED_LOGIN_FAILURE",
        severity="MEDIUM",
        evidence=f"{len(fails)} failed logins for {email} in {s['login_fail_window_s'] // 60} minutes.",
        recommended_action="Reset the password through an admin if this is unexpected. Account is not locked.",
    )


def share_denied_warning(user_id: str, permission: str) -> SecuritySignal:
    return SecuritySignal(
        signal="OPERATOR_SHARE_DENIED",
        severity="MEDIUM",
        evidence=f"{user_id} attempted a share without permission '{permission}'.",
        recommended_action="Use an authorised recipient and preview before sending.",
    )


def rapid_archive_warning(user_id: str, count: int, settings: Optional[dict[str, Any]] = None) -> Optional[SecuritySignal]:
    s = settings or guard_settings()
    if count < s["rapid_archive_limit"]:
        return None
    return SecuritySignal(
        signal="OPERATOR_RAPID_ARCHIVE",
        severity="HIGH",
        evidence=f"{user_id} archived {count} cases in one batch.",
        recommended_action="Confirm the archive was intentional. Nothing was sent externally.",
    )


def has_live_draft(case) -> bool:
    return any(d.status not in {DraftStatus.REJECTED, DraftStatus.SENT, DraftStatus.SIMULATED, DraftStatus.SEND_FAILED} for d in case.drafts)


def auto_draft_signal(count: int) -> SecuritySignal:
    return SecuritySignal(
        signal="AUTO_DRAFT_AFTER_REPEATED_ACTIONS",
        severity="LOW",
        evidence=f"{count} operator actions recorded; a draft was saved and was not sent.",
        recommended_action="Review the draft on the Draft Actions tab before approving.",
    )
