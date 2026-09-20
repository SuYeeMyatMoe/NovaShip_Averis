"""
Node 2 - Intent classifier (rules first, LLM for ambiguous cases).

Maps to both the product intent taxonomy and the hackathon category.
Rules are evidence-grounded (subject/body patterns from the real APRIL inbox).
"""
from __future__ import annotations

import re
from typing import Optional

from app.ai.llm import get_llm
from app.ai.trained_intent_classifier import predict_trained_intent
from app.contracts.schemas import (
    INTENT_TO_CATEGORY,
    EmailMessage,
    HackathonCategory,
    Intent,
    IntentClassification,
    Priority,
    SecurityAssessment,
    SecurityOutcome,
)

# Subject-line signals from the real inbox --------------------------------
BL_COMPARISON_SUBJECT = [
    r"\bTO CONFIRM DOCS\b", r"\bREQUEST BL DRAFT\b", r"\bDraft BL\b", r"\bamend BL\b",
    r"^(RE_\s*)?(AIE|AFPTME|AFRT|AFEMY)\s*-\s*",  # coded desk subjects: AIE - POD - CARRIER(BL#) - ...
]
BL_COMPARISON_BODY = [
    r"draft bill of lading", r"draft bl", r"verify the bl matches the si", r"check the draft bl against the si",
    r"attached are the si and draft bl", r"si and draft bl", r"compare the si and draft bl",
    r"send the draft bl", r"confirm the bl is in order", r"si and the .* for .* kindly confirm",
]
SI_REQUEST_SUBJECT = [r"^\s*(RE_\s*)?SI\s*-\s", r"\bCUST SI\b", r"\bREQUEST SI\b", r"\bSI NEEDED\b", r"\bLATEST SI\b"]
SI_REQUEST_BODY = [r"please find shipping instruction", r"shipping instruction for", r"documents required:", r"revert with draft bl once available"]
INVOICE_SUBJECT = [r"\bBILLING\b", r"\bMISSING GR\b", r"\bCANCEL INVOICE\b", r"\bLOCAL CHARGES\b", r"\bD & D charges\b", r"\bTotal Freight\b", r"\bTELEX RELEASE CHARGES\b"]
INVOICE_BODY = [r"\binvoice\b", r"\bthc\b", r"local charge", r"\bgr is still missing\b", r"reverse the pgi", r"detention charges", r"d&d"]
GENERAL_SUBJECT = [r"\bUPDATE SUMMARY\b", r"Berthing Report", r"_Reminder_", r"_RPA_", r"List of Outstanding BL", r"Pending BL Release",
                   r"Welcoming the New Year", r"_Approval Required_", r"Miss Connection", r"Delivery planning"]
GENERAL_BODY = [r"no action required", r"automated notification", r"berthing report", r"update summary", r"happy and prosperous", r"outstanding bl", r"rpa bot", r"submit si & aed"]
INFO_ONLY_BODY = [r"no action required", r"automated notification", r"for your information", r"fyi\b", r"wishing everyone"]

STRONG_NO_ACTION_INTENTS = {Intent.INFORMATION_ONLY, Intent.NO_ACTION_REQUIRED}


def _any(patterns: list[str], text: str) -> list[str]:
    return [p for p in patterns if re.search(p, text, re.I | re.M)]


def _strip_thread(body: str) -> str:
    """Only classify on the top message - quoted threads and signatures are noise."""
    cut = re.split(r"\n_{5,}|\nFrom: .*<.*>\nSent:", body, maxsplit=1)[0]
    cut = re.split(r"\nBest Regards,|\nRegards,|\nWarm regards,|\nThank you,|\nBest,", cut, maxsplit=1)[0]
    return cut


def classify_intent(email: EmailMessage, security: SecurityAssessment, has_attachments: bool, policy: Optional[dict] = None) -> IntentClassification:
    policy = policy or {}
    model_threshold = float(policy.get("intent_model_threshold", 0.65))
    model_rule_ceiling = float(policy.get("intent_model_rule_ceiling", 0.98))
    model_override_margin = float(policy.get("intent_model_override_margin", 0.05))
    llm_threshold = float(policy.get("intent_llm_threshold", 0.75))

    if security.outcome == SecurityOutcome.SPAM:
        return IntentClassification(
            intent=Intent.NO_ACTION_REQUIRED, hackathon_category=HackathonCategory.SPAM,
            action_required=False, priority=Priority.LOW, confidence=round(0.6 + security.score * 0.4, 2),
            rationale="Security precheck classified the message as SPAM: " + "; ".join(s.evidence for s in security.signals[:2]),
            decided_by="rule",
        )

    subject = email.subject or ""
    top = _strip_thread(email.body or "")
    scores: dict[HackathonCategory, float] = {c: 0.0 for c in HackathonCategory}
    evidence: dict[HackathonCategory, list[str]] = {c: [] for c in HackathonCategory}

    def bump(cat: HackathonCategory, pts: float, why: str) -> None:
        scores[cat] += pts
        evidence[cat].append(why)

    for p in _any(BL_COMPARISON_SUBJECT, subject):
        bump(HackathonCategory.BL_COMPARISON, 0.6, f"subject matches /{p}/")
    for p in _any(BL_COMPARISON_BODY, top):
        bump(HackathonCategory.BL_COMPARISON, 0.4, f"body matches /{p}/")
    for p in _any(SI_REQUEST_SUBJECT, subject):
        bump(HackathonCategory.SI_REQUEST, 0.7, f"subject matches /{p}/")
    for p in _any(SI_REQUEST_BODY, top):
        bump(HackathonCategory.SI_REQUEST, 0.35, f"body matches /{p}/")
    for p in _any(INVOICE_SUBJECT, subject):
        bump(HackathonCategory.INVOICE_QUERY, 0.6, f"subject matches /{p}/")
    for p in _any(INVOICE_BODY, top):
        bump(HackathonCategory.INVOICE_QUERY, 0.3, f"body matches /{p}/")
    for p in _any(GENERAL_SUBJECT, subject):
        bump(HackathonCategory.GENERAL, 0.6, f"subject matches /{p}/")
    for p in _any(GENERAL_BODY, top):
        bump(HackathonCategory.GENERAL, 0.3, f"body matches /{p}/")
    if has_attachments:
        bump(HackathonCategory.BL_COMPARISON, 0.3, "carries SI/BL attachments")
    if security.outcome == SecurityOutcome.SUSPICIOUS and not any(scores[c] > 0 for c in (HackathonCategory.BL_COMPARISON, HackathonCategory.SI_REQUEST, HackathonCategory.INVOICE_QUERY)):
        bump(HackathonCategory.SPAM, 0.4, "suspicious sender with no shipping context")

    # SI-in-body detail (SI_REQUEST) shouldn't be confused with BL comparison
    if re.search(r"POL:\s*\S", top) and re.search(r"POD:\s*\S", top) and re.search(r"Shipper:\s*\n", top):
        bump(HackathonCategory.SI_REQUEST, 0.5, "body contains a full SI block (POL/POD/Shipper)")

    best = max(scores, key=lambda c: scores[c])
    best_score = scores[best]
    ranked = sorted(scores.values(), reverse=True)
    margin = ranked[0] - (ranked[1] if len(ranked) > 1 else 0.0)
    confidence = 0.5 if best_score == 0 else min(0.99, 0.55 + best_score * 0.25 + margin * 0.2)
    decided_by = "rule"
    decision_note = ""
    if best_score == 0:
        best = HackathonCategory.GENERAL

    # A locally trained TF-IDF model handles weak/ambiguous rule outcomes.
    # Strong rule matches remain deterministic, and a missing model is safe.
    if confidence < model_rule_ceiling:
        prediction = predict_trained_intent(email, has_attachments)
        if prediction and prediction.confidence >= model_threshold:
            rule_best = best
            agrees = prediction.category == rule_best
            can_override = prediction.confidence >= confidence + model_override_margin
            if agrees or can_override:
                best = prediction.category
                confidence = max(confidence, prediction.confidence) if agrees else prediction.confidence
                decided_by = "hybrid" if agrees else "model"
                decision_note = (
                    f"trained model {prediction.model_version} predicted {best.value} "
                    f"with confidence {prediction.confidence:.2f}"
                )

    # LLM is the last tie-break when neither rules nor the local model is confident.
    if confidence < llm_threshold and get_llm().enabled:
        llm_cat = _llm_category(email)
        if llm_cat:
            decided_by = "llm" if llm_cat != best else "hybrid"
            best = llm_cat
            confidence = max(confidence, 0.8)
            decision_note = f"LLM tie-break selected {best.value}"

    intent, action_required, priority = _intent_from_category(best, top, has_attachments)
    rationale = "; ".join(evidence[best][:3]) if evidence[best] else f"No strong rule pattern matched for {best.value}."
    if decision_note:
        rationale = f"{decision_note}; {rationale}"
    return IntentClassification(
        intent=intent, hackathon_category=best, action_required=action_required, priority=priority,
        confidence=round(confidence, 2), rationale=rationale, decided_by=decided_by,
    )


def _intent_from_category(cat: HackathonCategory, top: str, has_attachments: bool) -> tuple[Intent, bool, Priority]:
    if cat == HackathonCategory.BL_COMPARISON:
        if re.search(r"\bamend\b|\bcorrect(ion)?\b|\bdiscrepanc", top, re.I):
            return Intent.DOCUMENT_CORRECTION, True, Priority.HIGH
        return Intent.DOCUMENT_VERIFICATION, True, (Priority.HIGH if has_attachments else Priority.MEDIUM)
    if cat == HackathonCategory.SI_REQUEST:
        return Intent.PREPARE_SHIPPING_INSTRUCTION, True, Priority.MEDIUM
    if cat == HackathonCategory.INVOICE_QUERY:
        return Intent.INVOICE_QUERY, True, Priority.MEDIUM
    if cat == HackathonCategory.SPAM:
        return Intent.NO_ACTION_REQUIRED, False, Priority.LOW
    # GENERAL
    if _any(INFO_ONLY_BODY, top):
        return Intent.INFORMATION_ONLY, False, Priority.LOW
    if re.search(r"kindly action|please submit|reminder", top, re.I):
        return Intent.OPERATIONAL_UPDATE, False, Priority.LOW
    return Intent.NO_ACTION_REQUIRED, False, Priority.LOW


_LLM_SYSTEM = """You classify shipping-operations emails for a documentation desk.
Return JSON: {"category": one of BL_COMPARISON|SI_REQUEST|INVOICE_QUERY|GENERAL|SPAM, "rationale": "<short evidence>"}.
BL_COMPARISON = asks to check/confirm a draft Bill of Lading against a Shipping Instruction (or requests the draft BL).
SI_REQUEST = provides or requests a Shipping Instruction. INVOICE_QUERY = invoices, charges, GR, billing.
GENERAL = internal updates/reports/HR/bots. SPAM = unsolicited/phishing."""


def _llm_category(email: EmailMessage) -> Optional[HackathonCategory]:
    data = get_llm().complete_json(_LLM_SYSTEM, f"Subject: {email.subject}\n\nBody:\n{email.body[:2500]}", max_tokens=200, purpose="intent")
    if not data:
        return None
    try:
        return HackathonCategory(str(data.get("category", "")).strip().upper())
    except ValueError:
        return None
