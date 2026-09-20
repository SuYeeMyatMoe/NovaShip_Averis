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
    (r"\b(approve|authori[sz]e|confirm)\b.*\b(for me|on my behalf|instead of me|this|it)\b", "I can't approve or confirm on a person's behalf. An authorised user must perform that action in the case workflow."),
    (r"\b(other|another|different)\s+case\b", "I can only answer about the current case. Open the other case to ask about it."),
    (r"\b(make up|invent|pretend|assume)\b.*\b(value|mismatch|field)", "I only report values that appear in the documents and the deterministic comparison; I won't invent values."),
]

_CASE_ID_RE = re.compile(r"\bcase(?:[_\s-][a-z]+)*[_\s-]?\d+\b", re.I)


def _normalise_case_id(value: str) -> str:
    normalised = re.sub(r"[_\s-]+", "_", value.strip().lower())
    numeric_suffix = re.search(r"(?:^|_)(\d+)$", normalised)
    if numeric_suffix:
        return str(int(numeric_suffix.group(1)))
    return normalised


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

_LLM_ACTION_VERBS = (
    r"sent|emailed|forwarded|dispatched|approved|authori[sz]ed|confirmed|granted"
)
_LLM_ACTION_MODIFIERS = r"(?:(?:already|just|now|successfully|recently)\s+)*"
_LLM_COMPLETED_ACTION = re.compile(
    rf"""
    (?:
        \b(?:i|we|the\s+assistant|novaship)
        (?:['’]ve|\s+(?:have|has))?\s+
        {_LLM_ACTION_MODIFIERS}(?:{_LLM_ACTION_VERBS})\b
      |
        \b(?:{_LLM_ACTION_VERBS})\b
        [^.!?\n]{{0,40}}\bby\s+(?:me|us|the\s+assistant|novaship)\b
      |
        \b(?:email|message|draft|notification|share|request|correction|it|approval|permission|authori[sz]ation)\b
        [^.!?\n]{{0,40}}\b
        (?:
            (?:
                (?:is|are|was|were)\s+
              |
                (?:has|have)\s+{_LLM_ACTION_MODIFIERS}been\s+
            )
            {_LLM_ACTION_MODIFIERS}(?:{_LLM_ACTION_VERBS})\b
          |
            {_LLM_ACTION_MODIFIERS}(?:went|gone)\s+out\b
        )
    )
    """,
    re.I | re.X,
)
_LLM_MISMATCH_CLAIM = re.compile(
    r"\b(?:mismatch(?:ed)?|differ(?:s|ed|ent)?|discrepanc(?:y|ies)|conflict(?:s|ing)?|"
    r"wrong|incorrect|does\s+not\s+match|do\s+not\s+match|not\s+matching|"
    r"requires?\s+correction|must\s+be\s+corrected)\b",
    re.I,
)
_LLM_MATCH_CLAIM = re.compile(
    r"\b(?:match(?:es|ed|ing)?|same|identical|consistent|align(?:s|ed)?)\b",
    re.I,
)

_LLM_FIELD_ALIASES = {
    "shipper": {"shipper", "exporter"},
    "consignee": {"consignee"},
    "notify_party": {"notify party", "notify"},
    "port_of_loading": {"port of loading", "load port", "loading port", "pol"},
    "port_of_discharge": {"port of discharge", "discharge port", "pod"},
    "container_count": {"container count", "container", "containers"},
    "gross_weight_kg": {"gross weight (kg)", "gross weight kg", "gross weight", "weight"},
}


def _normalise_claim_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _llm_output_is_grounded(text: str, ctx: dict[str, Any]) -> bool:
    """Reject model claims that cross deterministic data/action boundaries."""
    if _LLM_COMPLETED_ACTION.search(text):
        return False

    comparison = ctx.get("comparison") or {}
    field_context = {
        field["field"]: field
        for field in comparison.get("fields", [])
        if field.get("field") in SEVEN_FIELDS
    }
    flagged = {
        field_name
        for field_name, field in field_context.items()
        if field.get("result") == "MISMATCH"
    }
    segments = [
        segment.strip()
        for segment in re.split(r"(?<=[.!?;])\s+|\n+", text)
        if segment.strip()
    ]

    for field_name in SEVEN_FIELDS:
        aliases = {
            FIELD_LABELS[field_name],
            field_name.replace("_", " "),
            *_LLM_FIELD_ALIASES[field_name],
        }
        field_pattern = re.compile(
            r"\b(?:" + "|".join(re.escape(alias) for alias in aliases) + r")\b",
            re.I,
        )
        for segment in segments:
            if not field_pattern.search(segment):
                continue
            mismatch_claim = bool(_LLM_MISMATCH_CLAIM.search(segment))
            match_claim = not mismatch_claim and bool(_LLM_MATCH_CLAIM.search(segment))
            result = field_context.get(field_name, {}).get("result")
            if mismatch_claim and field_name not in flagged:
                return False
            if match_claim and result != "MATCH":
                return False

            known_values = {
                _normalise_claim_text(value)
                for value in (
                    field_context.get(field_name, {}).get("si"),
                    field_context.get(field_name, {}).get("bl"),
                )
                if value not in (None, "")
            }
            normalised_segment = _normalise_claim_text(segment)
            has_known_value = any(
                value in normalised_segment
                for value in known_values
            )
            if not has_known_value and not mismatch_claim and not match_claim:
                return False

    return True


def _llm_answer(question: str, ctx: dict[str, Any]) -> Optional[str]:
    llm = get_llm()
    if not llm.enabled:
        return None
    import json

    res = llm.complete(_LLM_SYSTEM, f"Context:\n{json.dumps(ctx, default=str)[:12000]}\n\nQuestion: {question}", max_tokens=500,
                       purpose="ask_ai", case_id=str(ctx.get("case_id") or "") or None)
    if not res or res.text.startswith("__LLM_ERROR__"):
        return None
    text = res.text.strip()
    if not _llm_output_is_grounded(text, ctx):
        return None
    return text


# ---------------------------------------------------------------------------
# Translation - preserves names, numbers, ports, units, identifiers
# ---------------------------------------------------------------------------
LANG_NAMES = {"en": "English", "zh": "Chinese", "ms": "Malay", "id": "Indonesian", "vi": "Vietnamese", "ko": "Korean", "ar": "Arabic", "fr": "French", "es": "Spanish", "de": "German", "ja": "Japanese"}
# Identifier masking now lives in app.ai.privacy (shared by translation and every provider call).


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
    """LLM translation with identifier protection (shared masker); deterministic passthrough offline."""
    from app.ai.privacy import IdentifierMasker

    llm = get_llm()
    masker = IdentifierMasker()
    masked = masker.mask(text)
    expected_tokens = masker.tokens_in(masked)
    if not llm.enabled:
        note = f"[Translation to {LANG_NAMES.get(target, target)} requires LLM_PROVIDER; showing original]\n\n"
        return note + text
    res = llm.complete(
        f"Translate the message to {LANG_NAMES.get(target, target)}. Keep every __IDn__ token, number, unit, company name and port name unchanged. Return only the translation.",
        masked, max_tokens=1200, purpose="translate", mask=True, masker=masker,
    )
    if not res or res.text.startswith("__LLM_ERROR__"):
        return text
    out = res.text
    if masker.tokens_in(out) != expected_tokens:
        return text
    return masker.unmask(out)


# ---------------------------------------------------------------------------
# Share / Notify Party message assistant (minimal-data sharing)
# ---------------------------------------------------------------------------
def build_share_message(case: CaseRecord, email: EmailMessage, recipient_label: str, is_external: bool, include_fields: Optional[list[str]], due_date: Optional[str]) -> tuple[str, dict[str, Any]]:
    cmp = case.comparison
    fields = []
    if cmp:
        chosen = cmp.mismatch_fields if include_fields is None else include_fields
        for f in cmp.fields:
            if f.field in chosen:
                fields.append({"field": f.field, "label": f.label, "result": f.result.value, "si": f.si_original, "bl": f.bl_original})
    subject = "" if is_external else email.subject
    summary = "" if is_external else (case.summary.text if case.summary else "")
    payload = {
        "case_id": case.id,
        "subject": subject,
        "summary": summary,
        "fields": fields,
        "required_action": case.recommendation.recommended_action if case.recommendation else "",
        "due_date": due_date,
        "recipient": recipient_label,
        "external": is_external,
    }
    lines = [f"Case {case.id}" + (f" - {subject}" if subject else "")]
    if summary:
        lines += ["", summary]
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
