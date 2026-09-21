"""
Policy learning from what people do at the security gate.

Rule (v1, `block_sender`): a flagged case (SUSPICIOUS / SECURITY_REVIEW) that a person *archived* without ever sending it
to human review or replying counts against its sender. Once a sender reaches `learning.min_archives` (3) the Policies
page proposes adding it to `security.blocked_senders`, which makes the precheck classify its future mail as SPAM at the
gate. The desk never applies the change itself: an Admin accepts the suggestion into the unsaved policy draft and saves a
new version, or dismisses it. Both decisions are audit events (`POLICY_SUGGESTION_ACCEPTED` / `_DISMISSED`) and nothing
else is stored, in the same spirit as the operator guard.

Suggestions are recomputed on every read from the case records + the audit log, so they need no migration and cannot go
stale: once the sender is in the policy, the recipe no longer fires.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any, Optional

from app.contracts.schemas import ActorType, CaseStatus, DraftStatus, SecurityOutcome

FREEMAIL = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "yahoo.com", "icloud.com", "msn.com"}
DEFAULT_LEARNING: dict[str, Any] = {"enabled": True, "min_archives": 3, "window_days": 90}
DECISION_ACTIONS = ["POLICY_SUGGESTION_ACCEPTED", "POLICY_SUGGESTION_DISMISSED"]
EVIDENCE_ACTIONS = ["COMPLETED", "REVIEW_REQUESTED"]
FLAGGED = {SecurityOutcome.SUSPICIOUS, SecurityOutcome.SECURITY_REVIEW}
REPLIED = {DraftStatus.SENT, DraftStatus.SIMULATED}


def learning_settings(policy: dict[str, Any]) -> dict[str, Any]:
    """`learning` policy section with defaults and clamps (unknown keys ignored)."""
    raw = (policy or {}).get("learning") or {}
    out = dict(DEFAULT_LEARNING)
    out["enabled"] = bool(raw.get("enabled", out["enabled"]))
    for key in ("min_archives", "window_days"):
        try:
            out[key] = max(1, int(raw.get(key, out[key])))
        except (TypeError, ValueError):
            pass
    return out


def sender_bucket(address: str) -> str:
    """The unit a suggestion is about: the whole address for free-mail senders, the domain otherwise."""
    addr = (address or "").strip().lower()
    if "@" not in addr:
        return addr
    domain = addr.split("@")[-1]
    return addr if domain in FREEMAIL else domain


def _audit_by_action(repo, actions: list[str], since: Optional[datetime]):
    fn = getattr(repo, "list_audit_by_action", None)
    if fn is not None:
        return fn(actions, since)
    return [e for e in repo.list_audit(None) if e.action in actions and (since is None or e.timestamp >= since)]


def decided_ids(repo) -> set[str]:
    """Suggestion ids an Admin already accepted or dismissed."""
    return {str((e.after or {}).get("suggestion_id")) for e in _audit_by_action(repo, DECISION_ACTIONS, None) if (e.after or {}).get("suggestion_id")}


def archived_at_gate(repo, settings: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """bucket -> flagged cases a person archived without action. Senders that were ever sent to human review or replied to
    are dropped entirely (the desk disagreed with itself, so nothing is learned)."""
    since = datetime.utcnow() - timedelta(days=settings["window_days"])
    events = _audit_by_action(repo, EVIDENCE_ACTIONS, since)
    archived_by: dict[str, Any] = {}
    reviewed: set[str] = set()
    for e in events:
        if not e.case_id or e.actor_type != ActorType.USER:
            continue
        if e.action == "REVIEW_REQUESTED":
            reviewed.add(e.case_id)
        elif e.action == "COMPLETED":
            cur = archived_by.get(e.case_id)
            if cur is None or e.timestamp > cur.timestamp:
                archived_by[e.case_id] = e
    emails = {e.id: e for e in repo.list_emails()}
    buckets: dict[str, list[dict[str, Any]]] = {}
    conflicted: set[str] = set()
    for c in repo.list_cases():
        if c.security.outcome not in FLAGGED:
            continue
        email = emails.get(c.source_email_id)
        if not email or not email.sender:
            continue
        bucket = sender_bucket(email.sender)
        replied = any(d.status in REPLIED for d in c.drafts)
        if c.id in reviewed or replied:
            conflicted.add(bucket)
            continue
        ev = archived_by.get(c.id)
        if c.status != CaseStatus.COMPLETED or ev is None or c.updated_at < since:
            continue
        buckets.setdefault(bucket, []).append({
            "case_id": c.id, "subject": email.subject or c.id, "sender": email.sender, "outcome": c.security.outcome.value,
            "status": c.status.value, "when": ev.timestamp.isoformat(), "by": ev.actor_id,
        })
    for b in conflicted:
        buckets.pop(b, None)
    return buckets


def _suggestion_id(recipe: str, bucket: str, to_value: Any) -> str:
    return f"sug_{recipe}_" + hashlib.sha1(f"{bucket}|{to_value}".encode()).hexdigest()[:10]


def recipe_block_sender(repo, policy: dict[str, Any], settings: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(open suggestions, progress of buckets still below the threshold)."""
    security = dict((policy or {}).get("security") or {})
    current = [str(x).lower() for x in security.get("blocked_senders") or []]
    protected = {str(x).lower() for x in (security.get("trusted_domains") or [])} | {str(x).lower() for x in (security.get("partner_domains") or [])}
    needed = settings["min_archives"]
    items: list[dict[str, Any]] = []
    progress: list[dict[str, Any]] = []
    for bucket, evidence in sorted(archived_at_gate(repo, settings).items(), key=lambda kv: (-len(kv[1]), kv[0])):
        domain = bucket.split("@")[-1]
        if bucket in current or domain in current or domain in protected:
            continue
        if len(evidence) < needed:
            progress.append({"recipe": "block_sender", "bucket": bucket, "count": len(evidence), "needed": needed})
            continue
        to_value = sorted(set(current + [bucket]))
        evidence.sort(key=lambda x: x["when"], reverse=True)
        items.append({
            "id": _suggestion_id("block", bucket, bucket), "recipe": "block_sender", "bucket": bucket, "count": len(evidence), "needed": needed,
            "title": f"Treat mail from {bucket} as spam",
            "rationale": f"{len(evidence)} flagged message{'s' if len(evidence) != 1 else ''} from {bucket} were archived without action. "
                         "Future mail from it would be classified SPAM at the gate and skip the security queue; nothing already archived changes.",
            "section": "security", "key": "blocked_senders", "from_value": list(current), "to_value": to_value,
            "proposed_section": {**security, "blocked_senders": to_value},
            "evidence": evidence[:6],
        })
    return items, progress[:3]


RECIPES = [recipe_block_sender]


def suggestions(repo, policy: dict[str, Any]) -> dict[str, Any]:
    """What the desk proposes right now, minus what an Admin already decided on."""
    settings = learning_settings(policy)
    if not settings["enabled"]:
        return {"items": [], "progress": [], "settings": settings, "total": 0}
    decided = decided_ids(repo)
    items: list[dict[str, Any]] = []
    progress: list[dict[str, Any]] = []
    for recipe in RECIPES:
        found, pending = recipe(repo, policy, settings)
        items.extend(s for s in found if s["id"] not in decided)
        progress.extend(pending)
    return {"items": items, "progress": progress[:3], "settings": settings, "total": len(items)}
