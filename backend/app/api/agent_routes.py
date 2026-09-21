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
    st = get_agent().state(case_id)
    case = get_repo().get_case(case_id)
    st["agent_run"] = case.agent_run.summary() if case and case.agent_run else None
    return st


@router.post("/agent/resume/{case_id}")
def agent_resume(case_id: str, decision: dict[str, Any], user: UserRecord = Depends(require("generate_draft"))):
    """Supply the HumanDecision that the interrupted graph is waiting for.
    Body: {"action": "approve|edit|reject|reassign|notify_party|retry|mark_no_action|complete", "draft_id"?, "edited_body"?, "note"?, "recipient"?}"""
    st = get_agent().state(case_id)
    if not st["paused"]:
        raise HTTPException(409, detail={"error": "graph is not waiting for a human decision", "category": "COMPARISON_ERROR", "recovery": "POST /agent/run/{case_id} first", "retryable": True})
    decision = {**decision, "user_id": user.id}
    return get_agent().resume(case_id, decision)


_BULK_RESUME_ACTIONS = {"retry", "request_review", "reject", "mark_no_action", "complete", "reassign"}


def _parallelism(requested: Optional[int]) -> int:
    import os

    default = int(os.environ.get("BATCH_PARALLELISM", "4") or 4)
    return max(1, min(int(requested or default), 16))


@router.post("/agent/run-batch")
def agent_run_batch(body: dict[str, Any], user: UserRecord = Depends(require("compare"))):
    """Run the LangGraph agent on many cases in parallel. Every case gets its own ok/paused/error result;
    one failure never stops the others. Paused cases are then resumed one by one (or via /agent/resume-batch)."""
    from concurrent.futures import ThreadPoolExecutor
    import time

    case_ids = [str(c).strip() for c in (body.get("case_ids") or []) if str(c).strip()]
    if not case_ids:
        raise HTTPException(400, detail={"error": "case_ids is required", "category": "DATABASE_ERROR"})
    if len(case_ids) > 200:
        raise HTTPException(400, detail={"error": "at most 200 cases per run", "category": "DATABASE_ERROR"})
    workers = _parallelism(body.get("parallel"))
    agent = get_agent()

    def one(case_id: str) -> dict[str, Any]:
        t0 = time.time()
        try:
            st = agent.run(case_id, actor_id=user.id, mode="batch")
            return {"ok": True, "paused": bool(st.get("paused")), "next": st.get("next") or [], "status": st.get("status"),
                    "interrupt": st.get("interrupt"), "agent_run": st.get("agent_run"), "ms": int((time.time() - t0) * 1000)}
        except ValueError as exc:
            return {"ok": False, "error": {"category": "DATABASE_ERROR", "message": str(exc), "retryable": False}, "ms": int((time.time() - t0) * 1000)}
        except Exception as exc:  # per-case isolation; the agent already audits pipeline errors
            return {"ok": False, "error": {"category": "COMPARISON_ERROR", "message": type(exc).__name__, "retryable": True}, "ms": int((time.time() - t0) * 1000)}

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="agent-batch") as pool:
        results = dict(zip(case_ids, pool.map(one, case_ids)))
    failed = [cid for cid, r in results.items() if not r["ok"]]
    paused = [cid for cid, r in results.items() if r["ok"] and r["paused"]]
    CaseServiceAudit.audit(user.id, "AGENT_BATCH_RUN", {"count": len(case_ids), "ok": len(case_ids) - len(failed), "failed": len(failed), "paused": len(paused), "parallel": workers})
    return {"results": results, "failed_ids": failed, "paused_ids": paused, "parallel": workers}


@router.post("/agent/resume-batch")
def agent_resume_batch(body: dict[str, Any], user: UserRecord = Depends(require("generate_draft"))):
    """Resume several paused graphs with the same non-sending decision (retry / request_review / reject / mark_no_action / complete / reassign).
    `approve`, `edit` and `notify_party` stay per case."""
    action = str(body.get("action") or "").strip()
    case_ids = [str(c).strip() for c in (body.get("case_ids") or []) if str(c).strip()]
    if action not in _BULK_RESUME_ACTIONS:
        raise HTTPException(400, detail={"error": f"bulk resume allows {', '.join(sorted(_BULK_RESUME_ACTIONS))}; approve/edit/notify_party are per case", "category": "AUTH_ERROR"})
    if not case_ids:
        raise HTTPException(400, detail={"error": "case_ids is required", "category": "DATABASE_ERROR"})
    agent = get_agent()
    results: dict[str, Any] = {}
    for cid in case_ids:
        try:
            st = agent.state(cid)
            if not st["paused"]:
                results[cid] = {"ok": False, "error": {"category": "COMPARISON_ERROR", "message": "graph is not waiting for a human decision", "retryable": False}}
                continue
            decision = {"action": action, "note": body.get("note"), "user_id": user.id, "draft_id": (st.get("interrupt") or {}).get("draft_id")}
            if body.get("recipient"):
                decision["recipient"] = body["recipient"]
            out = agent.resume(cid, decision)
            results[cid] = {"ok": True, "paused": bool(out.get("paused")), "status": out.get("status")}
        except HTTPException as exc:
            results[cid] = {"ok": False, "error": exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}}
        except Exception as exc:
            results[cid] = {"ok": False, "error": {"category": "COMPARISON_ERROR", "message": type(exc).__name__, "retryable": True}}
    CaseServiceAudit.audit(user.id, "AGENT_BATCH_RESUME", {"action": action, "count": len(case_ids), "ok": sum(1 for r in results.values() if r["ok"])})
    return {"results": results, "failed_ids": [cid for cid, r in results.items() if not r["ok"]]}


class CaseServiceAudit:
    """Desk-level audit rows for batch agent runs (no case id)."""

    @staticmethod
    def audit(user_id: str, action: str, after: dict[str, Any]) -> None:
        from app.contracts.schemas import ActorType
        from app.pipeline.orchestrator import Pipeline

        Pipeline(get_repo()).audit(None, ActorType.USER, user_id, action, after=after)


# ---------------------------------------------------------------- RAG
@router.get("/rag/info")
def rag_info(user: UserRecord = Depends(require("view_case"))):
    from app.api.routes import llm_posture

    return {**get_rag().info(), "llm": llm_posture()}


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
from app.services.reporting import field_stats as _field_stats, security_rows as _security_rows  # noqa: E402


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
