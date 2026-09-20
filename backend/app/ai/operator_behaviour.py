"""Evidence-based operator warnings and the 3-action auto-draft rule.

Document anomalies stay in anomaly.py. This module watches the logged-in user.
Warnings never lock an account or send email.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from typing import Optional

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

AUTO_DRAFT_THRESHOLD = 3
BURST_WINDOW_S = 60
BURST_LIMIT = 10
LOGIN_FAIL_WINDOW_S = 900
LOGIN_FAIL_LIMIT = 3

_burst: deque[tuple[str, datetime]] = deque()


def clear_operator_burst() -> None:
    _burst.clear()


def count_user_mutations(repo, case_id: str) -> int:
    return sum(
        1
        for event in repo.list_audit(case_id)
        if event.actor_type == ActorType.USER and event.action in MUTATION_ACTIONS
    )


def burst_warning(user_id: str, *, now: Optional[datetime] = None, limit: Optional[int] = None, window_s: Optional[int] = None) -> Optional[SecuritySignal]:
    limit = BURST_LIMIT if limit is None else limit
    window_s = BURST_WINDOW_S if window_s is None else window_s
    stamp = now or datetime.utcnow()
    _burst.append((user_id, stamp))
    cutoff = stamp - timedelta(seconds=window_s)
    while _burst and _burst[0][1] < cutoff:
        _burst.popleft()
    n = sum(1 for uid, _ in _burst if uid == user_id)
    if n < limit:
        return None
    return SecuritySignal(
        signal="OPERATOR_BURST_MUTATIONS",
        severity="MEDIUM",
        evidence=f"{n} operator mutations in {window_s}s by {user_id}.",
        recommended_action="Pause and confirm each action; this is a warning only.",
    )


def login_failed_warning(repo, email: str, *, now: Optional[datetime] = None) -> Optional[SecuritySignal]:
    stamp = now or datetime.utcnow()
    cutoff = stamp - timedelta(seconds=LOGIN_FAIL_WINDOW_S)
    fails = [
        event
        for event in repo.list_audit(None)
        if event.action == "LOGIN_FAILED"
        and event.timestamp >= cutoff
        and (event.after or {}).get("email") == email
    ]
    if len(fails) < LOGIN_FAIL_LIMIT:
        return None
    return SecuritySignal(
        signal="OPERATOR_REPEATED_LOGIN_FAILURE",
        severity="MEDIUM",
        evidence=f"{len(fails)} failed logins for {email} in {LOGIN_FAIL_WINDOW_S // 60} minutes.",
        recommended_action="Reset the password through an admin if this is unexpected. Account is not locked.",
    )


def share_denied_warning(user_id: str, permission: str) -> SecuritySignal:
    return SecuritySignal(
        signal="OPERATOR_SHARE_DENIED",
        severity="MEDIUM",
        evidence=f"{user_id} attempted a share without permission '{permission}'.",
        recommended_action="Use an authorised recipient and preview before sending.",
    )


def rapid_archive_warning(user_id: str, count: int) -> Optional[SecuritySignal]:
    if count < 5:
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
