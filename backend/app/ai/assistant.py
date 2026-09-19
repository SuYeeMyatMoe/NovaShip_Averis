"""
Ask-AI case assistant + translation + guardrails (Person 2).

Grounding rule: answers may only use the email, SI, Draft BL, deterministic
comparison, case history/audit and the active policy. Every answer carries
citations. The assistant NEVER:
  * asserts a mismatch the comparator did not produce,
  * treats the extracted Notify Party as authorisation to send,
  * sends anything itself,
  * reveals data from another case.
Rule-based answers cover the common questions offline; the LLM (if enabled)
answers free-form questions from the same grounded context and is post-checked.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from app.ai.llm import get_llm
from app.contracts.schemas import (
    FIELD_LABELS,
    SEVEN_FIELDS,
    AskResponse,
    AuditEvent,
    CaseRecord,
    DraftAction,
    EmailMessage,
    FieldResult,
)
from app.core.policy import explain_policy

BLOCKED_PATTERNS = [
    (r"\b(send|email|forward|dispatch)\b.*\b(now|directly|immediately|without (approval|review))", "I can prepare a draft, but sending requires a human to approve it in the Draft Actions tab."),
    (r"\b(bypass|skip|ignore)\b.*\b(approval|review|policy|check)", "I can't bypass the approval or policy gates. A supervisor can review the policy in Settings > Policies."),
    (r"\b(approve|authorize|confirm)\b.*\b(for me|on my behalf|instead of me|this|it)\b", "I can't approve or confirm on a person's behalf. An authorised user must perform that action in the case workflow."),
    (r"\b(other|another|different)\s+case\b", "I can only answer about the current case. Open the other case to ask about it."),
    (r"\b(make up|invent|pretend|assume)\b.*\b(value|mismatch|field)", "I only report values that appear in the documents and the deterministic comparison; I won't invent values."),
]

_CASE_ID_RE = re.compile(r"\bcase(?:[_\s-][a-z]+)*[_\s-]?\d+\b", re.I)


def _normalise_case_id(value: str) -> str:
    return re.sub(r"[_\s-]+", "_", value.strip().lower())


def _cite(kind: str, ref: str, snippet: str = "") -> dict[str, Any]:
    return {"kind": kind, "ref": ref, "snippet": snippet[:200]}


def build_context(case: CaseRecord, email: EmailMessage, audit: list[AuditEvent], policy: dict[str, Any]) -> dict[str, Any]:
    """The ONLY data the assistant may see."""
    cmp = case.comparison
    return {
        "case_id": case.id,
        "status": case.status.value,
        "intent": case.intent.value,
        "priority": case.priority.value,
        "email": {"from": email.sender, "subject": email.subject, "body": email.body[:3000], "received_at": email.received_at.isoformat()},
        "attachments": [{"id": a.id, "name": a.file_name, "type": a.detected_type.value, "status": a.extraction_status.value} for a in email.attachments],
        "comparison": None if not cmp else {
            "status": cmp.comparison_status.value, "message": cmp.message, "mismatch_count": cmp.mismatch_count,
            "fields": [{"field": f.field, "label": f.label, "result": f.result.value, "si": f.si_original, "bl": f.bl_original,
                        "confidence": f.confidence, "si_snippet": f.si_evidence.snippet, "bl_snippet": f.bl_evidence.snippet} for f in cmp.fields],
        },
        "summary": case.summary.text if case.summary else None,
        "recommendation": case.recommendation.model_dump() if case.recommendation else None,
        "assigned_user_id": case.assigned_user_id,
        "shared_with": case.shared_with,
        "drafts": [{"id": d.id, "type": d.draft_type, "status": d.status.value, "to": d.to, "subject": d.subject} for d in case.drafts],
        "audit": [{"at": a.timestamp.isoformat(), "actor": f"{a.actor_type.value}:{a.actor_id}", "action": a.action} for a in audit[-25:]],
        "policy": explain_policy(policy),
    }


def answer_question(question: str, case: CaseRecord, email: EmailMessage, audit: list[AuditEvent], policy: dict[str, Any], language: Optional[str] = None) -> AskResponse:
    q = question.strip()
    ql = q.lower()

    current_case_id = _normalise_case_id(case.id)
    mentioned_case_ids = {_normalise_case_id(match.group(0)) for match in _CASE_ID_RE.finditer(q)}
    if any(case_id != current_case_id for case_id in mentioned_case_ids):
        return AskResponse(
            answer="I can only answer about the current case. Open the other case to ask about it.",
            citations=[_cite("policy", "communication")],
            grounded=True,
            refused=True,
        )

    for pat, msg in BLOCKED_PATTERNS:
        if re.search(pat, ql):
            return AskResponse(answer=msg, citations=[_cite("policy", "communication")], grounded=True, refused=True)

    ctx = build_context(case, email, audit, policy)
    cmp = case.comparison
    cites: list[dict[str, Any]] = []

    def mismatch_fields():
        return [f for f in (cmp.fields if cmp else []) if f.result == FieldResult.MISMATCH]

    def match_fields():
        return [f for f in (cmp.fields if cmp else []) if f.result == FieldResult.MATCH]

    def review_fields():
        return [f for f in (cmp.fields if cmp else []) if f.result not in (FieldResult.MATCH, FieldResult.MISMATCH)]

    answer: Optional[str] = None

    if re.search(r"why .*mismatch|explain .*mismatch|what.*(wrong|differ)|which field", ql):
        if not cmp:
            answer = "No comparison has been run for this case yet (" + case.status.value + "). " + (case.summary.text if case.summary else "")
            cites.append(_cite("case", case.id))
        elif mismatch_fields():
            parts = []
            for f in mismatch_fields():
                parts.append(f"{f.label} - SI: \"{f.si_original}\" vs BL: \"{f.bl_original}\" (confidence {f.confidence:.2f}). {f.reason}")
                cites.append(_cite("si", f.si_evidence.document_id or "SI", f.si_evidence.snippet))
                cites.append(_cite("bl", f.bl_evidence.document_id or "BL", f.bl_evidence.snippet))
            answer = f"{len(mismatch_fields())} field(s) differ between the SI (source of truth) and the Draft BL:\n- " + "\n- ".join(parts)
            if review_fields():
                answer += "\n" + ", ".join(f.label for f in review_fields()) + " could not be decided and need human review."
        elif review_fields():
            answer = "No mismatch is asserted. These fields could not be decided: " + ", ".join(f"{f.label} ({f.reason})" for f in review_fields())
            cites += [_cite("comparison", f.field) for f in review_fields()]
        else:
            answer = cmp.message + " All seven fields match after safe normalization."
            cites += [_cite("comparison", f.field, f"{f.si_original} == {f.bl_original}") for f in cmp.fields]

    elif re.search(r"which .*match|fields? .*match|six fields|seven fields|what matches", ql):
        if not cmp:
            answer = "No comparison is available yet."
        else:
            mf = match_fields()
            answer = f"{len(mf)} of 7 fields match: " + ", ".join(f"{f.label} (\"{f.si_original}\")" for f in mf) + "."
            if mismatch_fields():
                answer += " Mismatch: " + ", ".join(f.label for f in mismatch_fields()) + "."
            cites += [_cite("comparison", f.field) for f in cmp.fields]

    elif re.search(r"si evidence|show .*evidence|where .*(si|bl)|source", ql):
        if not cmp:
            answer = "No document evidence yet - the case has not been compared."
        else:
            lines = []
            for f in cmp.fields:
                lines.append(f"{f.label}: SI [{f.si_evidence.label_found or '-'}] \"{f.si_evidence.snippet}\" | BL [{f.bl_evidence.label_found or '-'}] \"{f.bl_evidence.snippet}\"")
                cites.append(_cite("si", f.si_evidence.document_id or "SI", f.si_evidence.snippet))
                cites.append(_cite("bl", f.bl_evidence.document_id or "BL", f.bl_evidence.snippet))
            answer = "Evidence per field (label found -> line):\n- " + "\n- ".join(lines)

    elif re.search(r"notify party", ql) and re.search(r"who|what|which", ql):
        if cmp:
            f = next(x for x in cmp.fields if x.field == "notify_party")
            answer = f"Notify Party on the SI: \"{f.si_original}\"; on the Draft BL: \"{f.bl_original}\" -> {f.result.value}. "
            answer += "Note: the extracted Notify Party is a comparison value only - sharing with them requires selecting an approved recipient and human confirmation."
            cites += [_cite("si", f.si_evidence.document_id or "SI", f.si_evidence.snippet), _cite("bl", f.bl_evidence.document_id or "BL", f.bl_evidence.snippet), _cite("policy", "communication")]
        else:
            answer = "The Notify Party has not been extracted yet for this case."

    elif re.search(r"who (has been|was) (notified|shared)|shared with|notified", ql):
        shares = [a for a in audit if a.action in ("SHARE_SENT", "NOTIFY_PARTY_SENT", "SHARE_CREATED")]
        if shares:
            answer = "Shares/notifications on this case:\n- " + "\n- ".join(f"{a.timestamp:%Y-%m-%d %H:%M} {a.action} by {a.actor_id}: {(a.after or {}).get('recipient_label', '')}" for a in shares)
            cites += [_cite("audit", a.event_id) for a in shares]
        else:
            answer = "Nobody has been notified or shared on this case yet."
            cites.append(_cite("audit", case.id))

    elif re.search(r"who should (review|handle|own)|assign|responsible", ql):
        rec = case.recommendation
        answer = (f"Recommended owner: {rec.responsible_role.value} - {rec.recommended_action}" if rec else "No recommendation yet.")
        if case.assigned_user_id:
            answer += f" Currently assigned to {case.assigned_user_id}."
        cites.append(_cite("recommendation", case.id))

    elif re.search(r"summar", ql):
        answer = case.summary.text if case.summary else "No summary available."
        cites.append(_cite("summary", case.id))

    elif re.search(r"draft|correction email|write .*email", ql):
        d = case.drafts[-1] if case.drafts else None
        answer = (f"Draft ({d.draft_type}, {d.status.value}) to {', '.join(d.to)}:\nSubject: {d.subject}\n\n{d.body}" if d else "No draft exists; use 'Draft Reply' on the case to generate one.")
        answer += "\n\nThis draft is not sent until a human approves it."
        if d:
            cites.append(_cite("draft", d.id or "draft"))

    elif re.search(r"what changed|since (the )?last review|history|timeline", ql):
        recent = audit[-8:]
        answer = "Recent activity:\n- " + "\n- ".join(f"{a.timestamp:%Y-%m-%d %H:%M} [{a.actor_type.value}] {a.action}" for a in recent) if recent else "No activity recorded."
        cites += [_cite("audit", a.event_id) for a in recent]

    elif re.search(r"status|where (is|are) we|stage", ql):
        answer = f"Status: {case.status.value}. " + (case.summary.text if case.summary else "")
        cites.append(_cite("case", case.id))

    elif re.search(r"polic|rule|threshold", ql):
        answer = "Active policy:\n- " + "\n- ".join(ctx["policy"])
        cites.append(_cite("policy", "active"))

    elif re.search(r"translat", ql):
        target = _target_language(ql) or (language or "en")
        tr = translate_text(email.body, target)
        answer = f"Translation ({target}):\n\n{tr}"
        cites.append(_cite("email", email.id))

    elif re.search(r"(what|which) (is|are) the (shipper|consignee|port|container|weight)|(shipper|consignee|port of|container count|gross weight)\b", ql):
        if cmp:
            for f in cmp.fields:
                if f.field.replace("_", " ") in ql or f.label.lower() in ql or (f.field == "gross_weight_kg" and "weight" in ql) or (f.field == "container_count" and "container" in ql):
                    answer = f"{f.label}: SI = \"{f.si_original}\", Draft BL = \"{f.bl_original}\" -> {f.result.value} (confidence {f.confidence:.2f})."
                    cites += [_cite("si", f.si_evidence.document_id or "SI", f.si_evidence.snippet), _cite("bl", f.bl_evidence.document_id or "BL", f.bl_evidence.snippet)]
                    break
        if not answer:
            answer = "That field is not available until the case has been extracted and compared."

    if answer is None:
        # retrieval-augmented context: policy/glossary/ports + this case's own chunks only
        try:
            from app.agents.rag import get_rag

            hits = get_rag().search(q, case_id=case.id, k=5)
        except Exception:
            hits = []
        ctx["retrieved"] = [{"id": h["id"], "source": h["source"], "text": h["text"][:500]} for h in hits]
        llm_answer = _llm_answer(q, ctx)
        if llm_answer:
            cites = [_cite(h["source"].split(":")[0], h["id"], h["text"]) for h in hits] + [_cite("case", case.id)]
            return AskResponse(answer=llm_answer, citations=cites, grounded=True, generated_by="llm")
        if hits and hits[0]["score"] > 0.15:
            top = hits[0]
            answer = f"From the knowledge base [{top['id']}]:\n{top['text'][:600]}"
            cites += [_cite(h["source"].split(":")[0], h["id"], h["text"]) for h in hits[:3]]
            return AskResponse(answer=answer, citations=cites, grounded=True)
        answer = ("I can answer about this case's email, attachments, seven-field comparison, evidence, drafts, sharing history and policy. "
                  "Try: 'Why is this a mismatch?', 'Which fields match?', 'Show the SI evidence', 'Who is the Notify Party?', 'Summarize this case'.")
        cites.append(_cite("case", case.id))

    return AskResponse(answer=answer, citations=cites, grounded=True)


_LLM_SYSTEM = """You are the NovaShip case assistant. Answer ONLY from the JSON context. Cite fields by name.
Rules: never claim a mismatch unless comparison.fields shows result MISMATCH; never say you sent anything;
the Notify Party value is NOT permission to contact anyone; if the answer is not in the context, say so. Be concise."""


def _llm_answer(question: str, ctx: dict[str, Any]) -> Optional[str]:
    llm = get_llm()
    if not llm.enabled:
        return None
    import json

    res = llm.complete(_LLM_SYSTEM, f"Context:\n{json.dumps(ctx, default=str)[:12000]}\n\nQuestion: {question}", max_tokens=500)
    if not res or res.text.startswith("__LLM_ERROR__"):
        return None
    text = res.text.strip()
    # post-check: the LLM must not name a MISMATCH field the comparator did not flag
    flagged = {f["field"] for f in (ctx.get("comparison") or {}).get("fields", []) if f["result"] == "MISMATCH"}
    for f in SEVEN_FIELDS:
        if f not in flagged and re.search(rf"{FIELD_LABELS[f].lower()}[^.]*\bmismatch", text.lower()):
            return None
    return text


# ---------------------------------------------------------------------------
# Translation - preserves names, numbers, ports, units, identifiers
# ---------------------------------------------------------------------------
LANG_NAMES = {"en": "English", "zh": "Chinese", "ms": "Malay", "id": "Indonesian", "vi": "Vietnamese", "ko": "Korean", "ar": "Arabic", "fr": "French", "es": "Spanish", "de": "German", "ja": "Japanese"}
_PROTECT = re.compile(r"\b[A-Z]{3,}[A-Z0-9\-]*\d[A-Z0-9\-]*\b|\b\d[\d,\.]*\s?(KG|kg|MT|x\s?\d0'[A-Z]{2})\b|\b5[A-Z]{3}-\d{5}\b")
_COMPANY_NAME = re.compile(
    r"\b(?:[A-Z][A-Z0-9&.,'()/-]*\s+){1,8}"
    r"(?:LTD|LIMITED|LLC|INC|CORP|CORPORATION|SDN\s+BHD|PTE\s+LTD|CO\.,?\s+LTD)\b"
)
_PORT_LINE = re.compile(
    r"(?im)^((?:Port of Loading|Load Port|POL|Port of Discharge|Discharge Port|POD)\s*:\s*)(.+)$"
)


def _target_language(q: str) -> Optional[str]:
    for code, name in LANG_NAMES.items():
        if name.lower() in q or f" {code} " in f" {q} ":
            return code
    return None


def detect_language(text: str) -> str:
    if re.search(r"[一-鿿]", text):
        return "zh"
    if re.search(r"[؀-ۿ]", text):
        return "ar"
    if re.search(r"[가-힯]", text):
        return "ko"
    if re.search(r"[぀-ヿ]", text):
        return "ja"
    if re.search(r"\b(terima kasih|sila|dengan|untuk)\b", text, re.I):
        return "ms"
    return "en"


def translate_text(text: str, target: str) -> str:
    """LLM translation with identifier protection; deterministic passthrough offline."""
    llm = get_llm()
    protected: dict[str, str] = {}

    def _mask_value(value: str) -> str:
        key = f"__ID{len(protected)}__"
        protected[key] = value
        return key

    masked = _PORT_LINE.sub(lambda match: match.group(1) + _mask_value(match.group(2)), text)
    masked = _COMPANY_NAME.sub(lambda match: _mask_value(match.group(0)), masked)
    masked = _PROTECT.sub(lambda match: _mask_value(match.group(0)), masked)
    if not llm.enabled:
        note = f"[Translation to {LANG_NAMES.get(target, target)} requires LLM_PROVIDER; showing original]\n\n"
        return note + text
    res = llm.complete(
        f"Translate the message to {LANG_NAMES.get(target, target)}. Keep every __IDn__ token, number, unit, company name and port name unchanged. Return only the translation.",
        masked, max_tokens=1200,
    )
    if not res or res.text.startswith("__LLM_ERROR__"):
        return text
    out = res.text
    for k, v in protected.items():
        out = out.replace(k, v)
    return out


# ---------------------------------------------------------------------------
# Share / Notify Party message assistant (minimal-data sharing)
# ---------------------------------------------------------------------------
def build_share_message(case: CaseRecord, email: EmailMessage, recipient_label: str, is_external: bool, include_fields: list[str], due_date: Optional[str]) -> tuple[str, dict[str, Any]]:
    cmp = case.comparison
    fields = []
    if cmp:
        chosen = include_fields or cmp.mismatch_fields or []
        for f in cmp.fields:
            if f.field in chosen:
                fields.append({"field": f.field, "label": f.label, "result": f.result.value, "si": f.si_original, "bl": f.bl_original})
    payload = {
        "case_id": case.id,
        "subject": email.subject,
        "summary": case.summary.text if case.summary else "",
        "fields": fields,
        "required_action": case.recommendation.recommended_action if case.recommendation else "",
        "due_date": due_date,
        "recipient": recipient_label,
        "external": is_external,
    }
    lines = [f"Case {case.id} - {email.subject}", ""]
    lines.append(payload["summary"])
    if fields:
        lines.append("")
        lines.append("Fields requiring attention:")
        for f in fields:
            lines.append(f"  - {f['label']}: SI = {f['si']} | Draft BL = {f['bl']} ({f['result']})")
    if payload["required_action"]:
        lines += ["", f"Required action: {payload['required_action']}"]
    if due_date:
        lines.append(f"Due: {due_date}")
    if is_external:
        lines += ["", "(External share - only the fields above are disclosed; the original email body is not included.)"]
    return "\n".join(lines), payload
