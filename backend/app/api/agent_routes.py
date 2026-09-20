"""Agent (LangGraph), RAG, seven-field analytics, security queue and global audit endpoints."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException

from app.agents.graph import get_agent
from app.agents.rag import case_chunks, get_rag, knowledge_chunks
from app.auth.rbac import require
from app.config import get_repo
from app.contracts.schemas import FIELD_LABELS, SEVEN_FIELDS, UserRecord

router = APIRouter()


# ---------------------------------------------------------------- LangGraph agent
@router.get("/agent/graph")
def agent_graph(user: UserRecord = Depends(require("view_case"))):
    """Mermaid definition of the compiled StateGraph (rendered on /agent page)."""
    return {"mermaid": get_agent().mermaid(), "nodes": ["security_precheck", "security_agent", "classify", "detect_documents", "extract", "compare", "summarize_and_draft", "human_review", "notify"]}


@router.post("/agent/run/{case_id}")
def agent_run(case_id: str, user: UserRecord = Depends(require("compare"))):
    """Run the LangGraph pipeline for a case. Pauses at human_review (interrupt) when a decision is needed."""
    try:
        return get_agent().run(case_id, actor_id=user.id)
    except ValueError as exc:
        raise HTTPException(404, detail={"error": str(exc), "category": "DATABASE_ERROR"})


@router.get("/agent/state/{case_id}")
def agent_state(case_id: str, user: UserRecord = Depends(require("view_case"))):
    return get_agent().state(case_id)


@router.post("/agent/resume/{case_id}")
def agent_resume(case_id: str, decision: dict[str, Any], user: UserRecord = Depends(require("generate_draft"))):
    """Supply the HumanDecision that the interrupted graph is waiting for.
    Body: {"action": "approve|edit|reject|reassign|notify_party|retry|mark_no_action|complete", "draft_id"?, "edited_body"?, "note"?, "recipient"?}"""
    st = get_agent().state(case_id)
    if not st["paused"]:
        raise HTTPException(409, detail={"error": "graph is not waiting for a human decision", "category": "COMPARISON_ERROR", "recovery": "POST /agent/run/{case_id} first", "retryable": True})
    decision = {**decision, "user_id": user.id}
    return get_agent().resume(case_id, decision)


# ---------------------------------------------------------------- RAG
@router.get("/rag/info")
def rag_info(user: UserRecord = Depends(require("view_case"))):
    return get_rag().info()


@router.post("/rag/search")
def rag_search(body: dict[str, Any], user: UserRecord = Depends(require("view_case"))):
    q = str(body.get("query", "")).strip()
    if not q:
        raise HTTPException(400, detail={"error": "query required", "category": "EXTRACTION_ERROR"})
    return {"hits": get_rag().search(q, case_id=body.get("case_id"), k=int(body.get("k", 6)), sources=body.get("sources"))}


@router.post("/rag/reindex")
def rag_reindex(body: dict[str, Any] | None = None, user: UserRecord = Depends(require("edit_policy"))):
    """Rebuild the vector index (knowledge folder + all cases). ADMIN only."""
    rag = get_rag()
    n = rag.index(knowledge_chunks())
    repo = get_repo()
    total = 0
    if not (body or {}).get("knowledge_only"):
        batch = []
        for c in repo.list_cases():
            e = repo.get_email(c.source_email_id)
            if e:
                batch.extend(case_chunks(c, e))
            if len(batch) >= 300:
                total += rag.index(batch)
                batch = []
        total += rag.index(batch)
    return {"knowledge_chunks": n, "case_chunks": total, **rag.info()}


# ---------------------------------------------------------------- seven-field analytics
def _field_stats(cases) -> list[dict[str, Any]]:
    stats = {f: {"field": f, "label": FIELD_LABELS[f], "match": 0, "mismatch": 0, "review": 0, "examples": []} for f in SEVEN_FIELDS}
    for c in cases:
        if not c.comparison:
            continue
        for fld in c.comparison.fields:
            s = stats[fld.field]
            if fld.result.value == "MATCH":
                s["match"] += 1
            elif fld.result.value == "MISMATCH":
                s["mismatch"] += 1
                if len(s["examples"]) < 5:
                    s["examples"].append({"case_id": c.id, "si": fld.si_original, "bl": fld.bl_original})
            else:
                s["review"] += 1
    return list(stats.values())


def _security_rows(cases, emails: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for c in cases:
        if c.security.outcome.value == "SAFE" and not c.anomalies:
            continue
        e = emails.get(c.source_email_id)
        rows.append({"case_id": c.id, "subject": e.subject if e else "", "sender": e.sender if e else "", "outcome": c.security.outcome.value, "score": c.security.score,
                     "signals": [s.model_dump(mode="json") for s in c.security.signals], "anomalies": [a.model_dump(mode="json") for a in c.anomalies], "status": c.status.value})
    order = {"SECURITY_REVIEW": 0, "SUSPICIOUS": 1, "SPAM": 2, "SAFE": 3}
    rows.sort(key=lambda r: (order.get(r["outcome"], 9), -r["score"]))
    return rows


@router.get("/dashboard/fields")
def field_stats(user: UserRecord = Depends(require("view_case"))):
    """Per-field statistics across all compared cases: match / mismatch / review counts + example cases."""
    cases = get_repo().list_cases()
    compared = sum(1 for c in cases if c.comparison)
    return {"compared_cases": compared, "fields": _field_stats(cases)}


@router.get("/dashboard/field/{field}")
def field_cases(field: str, result: Optional[str] = None, user: UserRecord = Depends(require("view_case"))):
    if field not in SEVEN_FIELDS:
        raise HTTPException(404, detail={"error": f"unknown field {field}", "category": "COMPARISON_ERROR"})
    repo = get_repo()
    emails = {e.id: e for e in repo.list_emails()}
    rows = []
    for c in repo.list_cases():
        if not c.comparison:
            continue
        fld = next(x for x in c.comparison.fields if x.field == field)
        if result and fld.result.value != result:
            continue
        e = emails.get(c.source_email_id)
        rows.append({"case_id": c.id, "subject": e.subject if e else "", "sender": e.sender if e else "", "result": fld.result.value, "si": fld.si_original, "bl": fld.bl_original,
                     "confidence": fld.confidence, "status": c.status.value, "si_label": fld.si_evidence.label_found, "bl_label": fld.bl_evidence.label_found})
    rows.sort(key=lambda r: (r["result"] == "MATCH", r["case_id"]))
    return {"field": field, "label": FIELD_LABELS[field], "total": len(rows), "items": rows[:300]}


# ---------------------------------------------------------------- security queue + global audit
@router.get("/security/queue")
def security_queue(user: UserRecord = Depends(require("view_case"))):
    repo = get_repo()
    emails = {e.id: e for e in repo.list_emails()}
    rows = _security_rows(repo.list_cases(), emails)
    return {"total": len(rows), "items": rows}


@router.get("/audit")
def global_audit(limit: int = 200, action: Optional[str] = None, actor_type: Optional[str] = None, user: UserRecord = Depends(require("view_audit"))):
    repo = get_repo()
    rows = repo.list_audit()
    if action:
        rows = [a for a in rows if action.upper() in a.action]
    if actor_type:
        rows = [a for a in rows if a.actor_type.value == actor_type.upper()]
    rows = rows[-limit:]
    rows.reverse()
    return {"total": len(rows), "events": [a.model_dump(mode="json") for a in rows]}
