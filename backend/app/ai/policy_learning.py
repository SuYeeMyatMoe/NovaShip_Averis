"""
Policy learning from what people do at the security gate.

Evidence: a flagged case (SPAM / SUSPICIOUS / SECURITY_REVIEW) that a person *archived* without ever sending it to human
review or replying. Recipes look for what those mails have in common and propose one generalising rule each:

* `block_phrase`     - the same subject wording across >= `learning.min_archives` archived mails (any senders)
                       -> `security.blocked_phrases` + phrase: future mail with that wording is SPAM at the gate.
* `flag_domain_word` - the same word in >= N archived senders' domains -> `security.suspicious_domain_words` + word:
                       such senders score SUSPICIOUS (a person still decides).
* `block_sender`     - >= N archived mails from one sender -> `security.blocked_senders` + sender.

The desk never applies a change itself: an Admin accepts a suggestion into the unsaved policy draft and saves a new
version, or dismisses it. Both decisions are audit events (`POLICY_SUGGESTION_ACCEPTED` / `_DISMISSED`); nothing else is
stored, in the same spirit as the operator guard. Suggestions are recomputed on every read from the case records + the
audit log, so once a rule is in the policy the recipe no longer fires.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Optional

from app.ai.security_precheck import SUSPICIOUS_DOMAIN_WORDS, normalise_text
from app.contracts.schemas import ActorType, CaseStatus, DraftStatus, SecurityOutcome

FREEMAIL = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "yahoo.com", "icloud.com", "msn.com"}
DEFAULT_LEARNING: dict[str, Any] = {"enabled": True, "min_archives": 3, "window_days": 90}
DECISION_ACTIONS = ["POLICY_SUGGESTION_ACCEPTED", "POLICY_SUGGESTION_DISMISSED"]
EVIDENCE_ACTIONS = ["COMPLETED", "REVIEW_REQUESTED"]
FLAGGED = {SecurityOutcome.SPAM, SecurityOutcome.SUSPICIOUS, SecurityOutcome.SECURITY_REVIEW}   # anything the gate did not clear
REPLIED = {DraftStatus.SENT, DraftStatus.SIMULATED}
PHRASE_MIN_WORDS, PHRASE_MAX_WORDS = 3, 14
DOMAIN_WORD_MIN_LEN = 4


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
    """The unit the sender rule is about: the whole address for free-mail senders, the domain otherwise."""
    addr = (address or "").strip().lower()
    if "@" not in addr:
        return addr
    domain = addr.split("@")[-1]
    return addr if domain in FREEMAIL else domain


def _domain_of(address: str) -> str:
    addr = (address or "").strip().lower()
    return addr.split("@")[-1] if "@" in addr else addr


def domain_words(domain: str) -> set[str]:
    """Words of a sender domain without its TLD: 'logistics-deals.biz' -> {'logistics', 'deals'}."""
    parts = [p for p in domain.lower().split(".") if p]
    if len(parts) > 1:
        parts = parts[:-1]
    return {w for part in parts for w in part.split("-") if len(w) >= DOMAIN_WORD_MIN_LEN and not w.isdigit()}


def _audit_by_action(repo, actions: list[str], since: Optional[datetime]):
    fn = getattr(repo, "list_audit_by_action", None)
    if fn is not None:
        return fn(actions, since)
    return [e for e in repo.list_audit(None) if e.action in actions and (since is None or e.timestamp >= since)]


def decided_ids(repo) -> set[str]:
    """Suggestion ids an Admin already accepted or dismissed."""
    return {str((e.after or {}).get("suggestion_id")) for e in _audit_by_action(repo, DECISION_ACTIONS, None) if (e.after or {}).get("suggestion_id")}


class Evidence(dict):
    """One archived flagged mail (dict so it serialises as-is); `subject_norm` / `domain` are for the recipes."""


def gather(repo, settings: dict[str, Any]) -> dict[str, Any]:
    """Everything the recipes need in one pass: archived flagged mails, senders/subjects the desk treats as legitimate."""
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
    archived: list[Evidence] = []
    legit_subjects: list[str] = []      # normalised subjects of mail the desk treated as legitimate
    legit_domains: set[str] = set()     # sender domains the desk replied to / reviewed / cleared as SAFE
    conflicted: set[str] = set()        # sender buckets with contradicting decisions
    for c in repo.list_cases():
        email = emails.get(c.source_email_id)
        if not email or not email.sender:
            continue
        bucket = sender_bucket(email.sender)
        replied = any(d.status in REPLIED for d in c.drafts)
        flagged = c.security.outcome in FLAGGED
        if not flagged or c.id in reviewed or replied:
            legit_subjects.append(normalise_text(email.subject))
            if not flagged or replied or c.id in reviewed:
                legit_domains.add(_domain_of(email.sender))
            if flagged:
                conflicted.add(bucket)
            continue
        ev = archived_by.get(c.id)
        if c.status != CaseStatus.COMPLETED or ev is None or c.updated_at < since:
            continue
        archived.append(Evidence({
            "case_id": c.id, "subject": email.subject or c.id, "sender": email.sender, "outcome": c.security.outcome.value,
            "status": c.status.value, "when": ev.timestamp.isoformat(), "by": ev.actor_id,
            "subject_norm": normalise_text(email.subject), "domain": _domain_of(email.sender), "bucket": bucket,
        }))
    return {"archived": archived, "legit_subjects": legit_subjects, "legit_domains": legit_domains, "conflicted": conflicted}


def _public(ev: Evidence) -> dict[str, Any]:
    return {k: v for k, v in ev.items() if k not in ("subject_norm", "domain", "bucket")}


def _suggestion_id(recipe: str, bucket: str, to_value: Any) -> str:
    return f"sug_{recipe}_" + hashlib.sha1(f"{bucket}|{to_value}".encode()).hexdigest()[:10]


def _ngrams(norm: str) -> set[str]:
    words = norm.split()
    out: set[str] = set()
    for n in range(PHRASE_MIN_WORDS, min(PHRASE_MAX_WORDS, len(words)) + 1):
        for i in range(len(words) - n + 1):
            out.add(" ".join(words[i:i + n]))
    return out


def recipe_block_phrase(data: dict[str, Any], policy: dict[str, Any], settings: dict[str, Any]):
    """Subject wording shared by >= min_archives archived mails (any senders) and by no legitimate mail."""
    security = dict((policy or {}).get("security") or {})
    current = [normalise_text(str(x)) for x in security.get("blocked_phrases") or []]
    needed = settings["min_archives"]
    by_phrase: dict[str, list[Evidence]] = defaultdict(list)
    for ev in data["archived"]:
        if any(p and f" {p} " in f" {ev['subject_norm']} " for p in current):
            continue  # already covered by the policy
        for g in _ngrams(ev["subject_norm"]):
            by_phrase[g].append(ev)
    legit = [f" {s} " for s in data["legit_subjects"]]
    # every window of one campaign subject is supported by the same mails: keep only the longest phrase per support set
    by_support: dict[frozenset, tuple[str, list[Evidence]]] = {}
    for g, evs in by_phrase.items():
        if any(f" {g} " in s for s in legit):
            continue
        key = frozenset(e["case_id"] for e in evs)
        best = by_support.get(key)
        if best is None or len(g.split()) > len(best[0].split()) or (len(g.split()) == len(best[0].split()) and g < best[0]):
            by_support[key] = (g, evs)
    # most evidence first, then longest; a phrase contained in an already chosen one is redundant
    candidates = sorted(by_support.values(), key=lambda kv: (-len(kv[1]), -len(kv[0].split()), kv[0]))
    items: list[dict[str, Any]] = []
    progress: list[dict[str, Any]] = []
    chosen: list[str] = []
    for g, evs in candidates:
        if any(f" {g} " in f" {c} " for c in chosen):
            continue
        chosen.append(g)
        n = len({e["case_id"] for e in evs})
        label = f'subject "{g[:48]}{"…" if len(g) > 48 else ""}"'
        if n < needed:
            if n >= 2:
                progress.append({"recipe": "block_phrase", "kind": "phrase", "bucket": g, "label": label, "count": n, "needed": needed})
            continue
        senders = len({e["domain"] for e in evs})
        to_value = sorted(set(current + [g]))
        evs = sorted(evs, key=lambda x: x["when"], reverse=True)
        items.append({
            "id": _suggestion_id("phrase", g, g), "recipe": "block_phrase", "bucket": g, "count": n, "needed": needed,
            "title": f'Treat mail saying "{g[:60]}{"…" if len(g) > 60 else ""}" as spam',
            "rationale": f"{n} archived mails from {senders} different sender{'s' if senders != 1 else ''} share this wording and no legitimate mail does. "
                         "Future mail containing it is classified SPAM at the gate and skips the queue, whoever sends it.",
            "section": "security", "key": "blocked_phrases", "from_value": list(current), "to_value": to_value,
            "proposed_section": {**security, "blocked_phrases": to_value},
            "evidence": [_public(e) for e in evs[:6]],
        })
    return items, progress


def recipe_flag_domain_word(data: dict[str, Any], policy: dict[str, Any], settings: dict[str, Any]):
    """A word that keeps appearing in archived senders' domains and never in a legitimate sender's domain."""
    security = dict((policy or {}).get("security") or {})
    current = [str(x).lower() for x in security.get("suspicious_domain_words") or []]
    known = set(SUSPICIOUS_DOMAIN_WORDS) | set(current)
    protected = {str(x).lower() for x in (security.get("trusted_domains") or [])} | {str(x).lower() for x in (security.get("partner_domains") or [])} | data["legit_domains"]
    protected_words = {w for d in protected for w in domain_words(d)}
    needed = settings["min_archives"]
    by_word: dict[str, dict[str, list[Evidence]]] = defaultdict(lambda: defaultdict(list))
    for ev in data["archived"]:
        if ev["domain"] in FREEMAIL:
            continue
        for w in domain_words(ev["domain"]):
            by_word[w][ev["domain"]].append(ev)
    items: list[dict[str, Any]] = []
    progress: list[dict[str, Any]] = []
    for w, by_domain in sorted(by_word.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if any(k in w for k in known) or w in protected_words:
            continue
        n = len(by_domain)  # distinct sender domains, so one noisy sender cannot teach a word alone
        label = f'domain word "{w}"'
        if n < needed:
            if n >= 2:
                progress.append({"recipe": "flag_domain_word", "kind": "domain_word", "bucket": w, "label": label, "count": n, "needed": needed})
            continue
        evs = sorted((e for evs in by_domain.values() for e in evs), key=lambda x: x["when"], reverse=True)
        to_value = sorted(set(current + [w]))
        items.append({
            "id": _suggestion_id("dword", w, w), "recipe": "flag_domain_word", "bucket": w, "count": n, "needed": needed,
            "title": f'Flag senders whose domain contains "{w}"',
            "rationale": f"{n} different archived sender domains contain \"{w}\" ({', '.join(sorted(by_domain)[:4])}) and no legitimate sender does. "
                         "Such mail is scored SUSPICIOUS at the gate, so a person still decides.",
            "section": "security", "key": "suspicious_domain_words", "from_value": list(current), "to_value": to_value,
            "proposed_section": {**security, "suspicious_domain_words": to_value},
            "evidence": [_public(e) for e in evs[:6]],
        })
    return items, progress


def recipe_block_sender(data: dict[str, Any], policy: dict[str, Any], settings: dict[str, Any]):
    """>= min_archives archived mails from one sender (address for free-mail, domain otherwise)."""
    security = dict((policy or {}).get("security") or {})
    current = [str(x).lower() for x in security.get("blocked_senders") or []]
    protected = {str(x).lower() for x in (security.get("trusted_domains") or [])} | {str(x).lower() for x in (security.get("partner_domains") or [])}
    needed = settings["min_archives"]
    by_bucket: dict[str, list[Evidence]] = defaultdict(list)
    for ev in data["archived"]:
        if ev["bucket"] not in data["conflicted"]:
            by_bucket[ev["bucket"]].append(ev)
    items: list[dict[str, Any]] = []
    progress: list[dict[str, Any]] = []
    for bucket, evs in sorted(by_bucket.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        domain = bucket.split("@")[-1]
        if bucket in current or domain in current or domain in protected:
            continue
        n = len(evs)
        if n < needed:
            if n >= 2:
                progress.append({"recipe": "block_sender", "kind": "sender", "bucket": bucket, "label": f"sender {bucket}", "count": n, "needed": needed})
            continue
        to_value = sorted(set(current + [bucket]))
        evs = sorted(evs, key=lambda x: x["when"], reverse=True)
        items.append({
            "id": _suggestion_id("block", bucket, bucket), "recipe": "block_sender", "bucket": bucket, "count": n, "needed": needed,
            "title": f"Treat mail from {bucket} as spam",
            "rationale": f"{n} flagged messages from {bucket} were archived without action. "
                         "Future mail from it would be classified SPAM at the gate and skip the security queue; nothing already archived changes.",
            "section": "security", "key": "blocked_senders", "from_value": list(current), "to_value": to_value,
            "proposed_section": {**security, "blocked_senders": to_value},
            "evidence": [_public(e) for e in evs[:6]],
        })
    return items, progress


RECIPES = [recipe_block_phrase, recipe_flag_domain_word, recipe_block_sender]


def suggestions(repo, policy: dict[str, Any]) -> dict[str, Any]:
    """What the desk proposes right now, minus what an Admin already decided on."""
    settings = learning_settings(policy)
    if not settings["enabled"]:
        return {"items": [], "progress": [], "settings": settings, "total": 0}
    decided = decided_ids(repo)
    data = gather(repo, settings)
    items: list[dict[str, Any]] = []
    progress: list[dict[str, Any]] = []
    for recipe in RECIPES:
        found, pending = recipe(data, policy, settings)
        items.extend(s for s in found if s["id"] not in decided)
        progress.extend(pending)
    progress.sort(key=lambda p: (-p["count"], p["label"]))
    return {"items": items, "progress": progress[:3], "settings": settings, "total": len(items)}
