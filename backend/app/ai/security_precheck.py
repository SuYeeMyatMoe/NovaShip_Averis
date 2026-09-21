"""
Node 1 - Security precheck (deterministic rules; evidence-grounded).

Outputs SAFE | SPAM | SUSPICIOUS | SECURITY_REVIEW with signals.
Never executes attachments. Never accuses a sender without evidence.
"""
from __future__ import annotations

import re
from typing import Iterable

from app.contracts.schemas import (
    AttachmentMeta,
    EmailMessage,
    SecurityAssessment,
    SecurityOutcome,
    SecuritySignal,
)
from app.readers.document_reader import BLOCKED_EXTENSIONS, SUPPORTED_EXTENSIONS, file_extension

SPAM_PHRASES = [
    "gift card", "claim now", "claim your", "you have won", "you have been selected", "congratulations",
    "verify your account", "verify account", "storage limit", "mailbox has exceeded", "storage is full",
    "avoid deactivation", "avoid suspension", "update your account", "unpaid customs fee", "parcel is on hold",
    "confirm payment of", "90% off", "limited time offer", "buy now", "weird trick", "hot singles",
    "bitcoin", "guaranteed 300%", "investment opportunity", "business proposal", "bank details",
    "undelivered messages", "free iphone", "pay $1 shipping", "usd 4.5 million", "kindly confirm your bank",
]
SUSPICIOUS_TLDS = (".info", ".biz", ".co", ".net", ".org", ".xyz", ".top", ".club")
SUSPICIOUS_DOMAIN_WORDS = ("prize", "claim", "verify", "parcel", "track", "crypto", "invest", "deals", "secure-mailbox", "webmail")
URL_RE = re.compile(r"https?://[^\s)>\]]+", re.I)
SHORTENERS = ("bit.ly", "tinyurl", "t.co", "goo.gl")

TRUSTED_DOMAINS = {"aprilasia.com", "april.com.my", "aprilinternational.com"}
KNOWN_PARTNER_DOMAINS = {
    "fujitogrp.com", "safqa.co.ke", "psabdp.com", "roxcel.at", "ifpla.com", "algurg.ae", "vitalsolutions.sg",
}
BYPASS_PHRASES = ("skip the check", "skip verification", "bypass", "no need to verify", "do not audit", "ignore the policy")


def _domain(addr: str) -> str:
    return addr.split("@")[-1].lower().strip() if "@" in addr else addr.lower()


_TAG_RE = re.compile(r"^\s*\[[^\]]*\]\s*")
_WORD_RE = re.compile(r"[a-z0-9]+")


def normalise_text(text: str) -> str:
    """Lowercase words only: a leading [tag] and pure numbers are dropped, tokens of 1-2 characters too.
    The same function normalises learned `blocked_phrases` and the mail they are matched against."""
    body = _TAG_RE.sub("", (text or "").lower())
    return " ".join(w for w in _WORD_RE.findall(body) if not w.isdigit() and len(w) > 2)


def assess_security(email: EmailMessage, attachments: Iterable[AttachmentMeta], policy: dict | None = None) -> SecurityAssessment:
    policy = policy or {}
    blocked_ext = set(policy.get("blocked_attachment_types", [])) | BLOCKED_EXTENSIONS
    max_attachments = int(policy.get("max_attachments", 10))
    trusted = TRUSTED_DOMAINS | set(policy.get("trusted_domains", []))
    partners = KNOWN_PARTNER_DOMAINS | set(policy.get("partner_domains", []))
    blocked = {str(x).strip().lower() for x in policy.get("blocked_senders", []) if x}
    blocked_phrases = [normalise_text(str(x)) for x in policy.get("blocked_phrases", []) if str(x).strip()]
    lure_words = tuple(SUSPICIOUS_DOMAIN_WORDS) + tuple(str(w).lower() for w in policy.get("suspicious_domain_words", []) if w)

    text = f"{email.subject}\n{email.body}".lower()
    signals: list[SecuritySignal] = []
    score = 0.0

    hits = [p for p in SPAM_PHRASES if p in text]
    if hits:
        score += min(0.6, 0.25 * len(hits))
        signals.append(SecuritySignal(
            signal="SPAM_PHRASES", severity="HIGH" if len(hits) >= 2 else "MEDIUM",
            evidence="Matched phrases: " + ", ".join(f"'{h}'" for h in hits[:4]),
            recommended_action="Treat as spam; do not act on links or requests.",
        ))

    dom = _domain(email.sender)
    if dom in trusted:
        score -= 0.2
    elif dom in partners:
        pass
    else:
        if any(w in dom for w in lure_words):
            score += 0.35
            signals.append(SecuritySignal(
                signal="SUSPICIOUS_SENDER_DOMAIN", severity="MEDIUM",
                evidence=f"Sender domain '{dom}' contains a lure keyword.",
                recommended_action="Verify the sender out-of-band before replying.",
            ))
        elif dom.endswith(SUSPICIOUS_TLDS) and not dom.endswith(".co.ke"):
            score += 0.15
            signals.append(SecuritySignal(
                signal="UNKNOWN_SENDER_DOMAIN", severity="LOW",
                evidence=f"Sender domain '{dom}' is not a known partner or internal domain.",
                recommended_action="Confirm the counterparty identity if the email requests action.",
            ))
        else:
            signals.append(SecuritySignal(
                signal="UNKNOWN_SENDER", severity="LOW",
                evidence=f"Sender domain '{dom}' is not on the trusted/partner list.",
                recommended_action="No action; monitor.",
            ))

    urls = URL_RE.findall(email.body)
    bad_urls = [u for u in urls if any(s in u.lower() for s in SHORTENERS) or any(w in u.lower() for w in SUSPICIOUS_DOMAIN_WORDS)]
    if bad_urls:
        score += 0.25
        signals.append(SecuritySignal(
            signal="SUSPICIOUS_LINK", severity="HIGH",
            evidence="Links: " + ", ".join(bad_urls[:3]),
            recommended_action="Do not click. Quarantine.",
        ))

    if any(p in text for p in BYPASS_PHRASES):
        score += 0.3
        signals.append(SecuritySignal(
            signal="POLICY_BYPASS_REQUEST", severity="HIGH",
            evidence="Message asks to skip or bypass verification/policy.",
            recommended_action="Route to supervisor; do not bypass policy.",
        ))

    atts = list(attachments)
    if len(atts) > max_attachments:
        score += 0.2
        signals.append(SecuritySignal(
            signal="ATTACHMENT_FLOOD", severity="MEDIUM",
            evidence=f"{len(atts)} attachments exceeds policy max {max_attachments}.",
            recommended_action="Security review before processing.",
        ))
    for a in atts:
        ext = file_extension(a.file_name)
        if ext in blocked_ext:
            score += 0.6
            signals.append(SecuritySignal(
                signal="BLOCKED_ATTACHMENT_TYPE", severity="CRITICAL",
                evidence=f"Attachment '{a.file_name}' has blocked type '{ext}'.",
                recommended_action="Quarantine attachment; security review.",
            ))
        elif ext not in SUPPORTED_EXTENSIONS:
            score += 0.1
            signals.append(SecuritySignal(
                signal="UNSUPPORTED_ATTACHMENT_TYPE", severity="LOW",
                evidence=f"Attachment '{a.file_name}' type '{ext}' is not processed automatically.",
                recommended_action="Ask sender for PDF, DOCX, XLSX, an image scan or plain text.",
            ))
        if a.is_duplicate_of:
            signals.append(SecuritySignal(
                signal="DUPLICATE_DOCUMENT", severity="LOW",
                evidence=f"Attachment '{a.file_name}' is byte-identical to {a.is_duplicate_of}.",
                recommended_action="Link to the existing document instead of re-processing.",
            ))
    if email.is_duplicate_of:
        signals.append(SecuritySignal(
            signal="DUPLICATE_MESSAGE", severity="LOW",
            evidence=f"Message content is identical to {email.is_duplicate_of}.",
            recommended_action="Attach to the existing case; do not create a new one.",
        ))

    # learned block list (policy `security.blocked_senders`, filled only by an Admin accepting a suggestion): straight to SPAM
    sender_lc = (email.sender or "").strip().lower()
    is_blocked = bool(blocked) and (sender_lc in blocked or dom in blocked)
    if is_blocked:
        score = max(score, 1.0)
        signals.append(SecuritySignal(
            signal="BLOCKED_SENDER", severity="HIGH",
            evidence=f"Sender '{sender_lc}' is on the desk's blocked list (learned from archived mail).",
            recommended_action="Treat as spam; no action needed.",
        ))
    if blocked_phrases:
        normalised = f" {normalise_text(email.subject)} {normalise_text(email.body)} "
        hit = next((p for p in blocked_phrases if p and f" {p} " in normalised), None)
        if hit:
            is_blocked = True
            score = max(score, 1.0)
            signals.append(SecuritySignal(
                signal="BLOCKED_PHRASE", severity="HIGH",
                evidence=f"Wording matches a phrase the desk learned to archive: '{hit[:80]}'.",
                recommended_action="Treat as spam; no action needed.",
            ))

    score = max(0.0, min(1.0, score))
    if is_blocked:
        outcome = SecurityOutcome.SPAM
    elif any(s.severity == "CRITICAL" for s in signals) or any(s.signal == "POLICY_BYPASS_REQUEST" for s in signals):
        outcome = SecurityOutcome.SECURITY_REVIEW
    elif score >= 0.5:
        outcome = SecurityOutcome.SPAM
    elif score >= 0.3:
        outcome = SecurityOutcome.SUSPICIOUS
    else:
        outcome = SecurityOutcome.SAFE

    rationale = {
        SecurityOutcome.SAFE: "No spam or malicious indicators found.",
        SecurityOutcome.SPAM: "Multiple spam indicators (phrases/links/sender) with no shipping context.",
        SecurityOutcome.SUSPICIOUS: "Some indicators present; proceed with caution.",
        SecurityOutcome.SECURITY_REVIEW: "Critical indicator present; requires security review before any action.",
    }[outcome]
    return SecurityAssessment(outcome=outcome, score=round(score, 3), signals=signals, rationale=rationale)
