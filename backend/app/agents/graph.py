"""
LangGraph StateGraph for a NovaShip case.

    security_precheck -> security_agent -+-> [SECURITY_REVIEW] -> human_review -> notify -> END
                                         +-> [SPAM]            -> summarize_and_draft -> END (no action)
                                         +-> classify -+-> [verification] -> detect_documents -+-> extract -> compare -> summarize_and_draft
                                                       |                                       +-> (waiting docs / unreadable) -> summarize_and_draft
                                                       +-> [other / no_action] -> summarize_and_draft
    summarize_and_draft -+-> needs_human -> human_review (interrupt) -> notify -> END
                         +-> END

Checkpointer: MemorySaver (default) or Postgres (LANGGRAPH_CHECKPOINT=postgres + LANGGRAPH_PG_URL,
e.g. the Supabase connection string) so interrupted graphs survive restarts.
thread_id == case_id, so one paused conversation per case.
"""
from __future__ import annotations

import atexit
import os
import time
from datetime import datetime
from contextlib import ExitStack
from functools import lru_cache
from typing import Any, Optional

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.agents.nodes import Nodes
from app.agents.state import GraphState
from app.contracts.schemas import ActorType, AgentRunInfo
from app.repositories.base import BaseRepository


# Keep context-managed Postgres checkpointer resources alive for the process lifetime.
# Calling PostgresSaver.from_conn_string(...).__enter__() on a temporary context
# manager closes its connection as soon as that temporary is released.
_CHECKPOINTER_RESOURCES = ExitStack()
atexit.register(_CHECKPOINTER_RESOURCES.close)


def _route_after_security(state: GraphState) -> str:
    return {"security_review": "human_review", "no_action": "summarize_and_draft"}.get(state.get("route", ""), "classify")


def _route_after_classify(state: GraphState) -> str:
    return "detect_documents" if state.get("route") == "verification" else "summarize_and_draft"


def _route_after_documents(state: GraphState) -> str:
    return "extract" if state.get("route") == "extract" else "summarize_and_draft"


def _route_after_summary(state: GraphState) -> str:
    return "human_review" if state.get("needs_human") else END


def _route_after_human(state: GraphState) -> str:
    return "security_precheck" if state.get("route") == "retry" else "notify"


def build_graph(repo: BaseRepository, checkpointer=None):
    nodes = Nodes(repo)
    g = StateGraph(GraphState)
    g.add_node("security_precheck", nodes.security_precheck)
    g.add_node("security_agent", nodes.security_agent)
    g.add_node("classify", nodes.classify)
    g.add_node("detect_documents", nodes.detect_documents)
    g.add_node("extract", nodes.extract)
    g.add_node("compare", nodes.compare)
    g.add_node("summarize_and_draft", nodes.summarize_and_draft)
    g.add_node("human_review", nodes.human_review)
    g.add_node("notify", nodes.notify)

    g.add_edge(START, "security_precheck")
    g.add_edge("security_precheck", "security_agent")
    g.add_conditional_edges("security_agent", _route_after_security, ["human_review", "summarize_and_draft", "classify"])
    g.add_conditional_edges("classify", _route_after_classify, ["detect_documents", "summarize_and_draft"])
    g.add_conditional_edges("detect_documents", _route_after_documents, ["extract", "summarize_and_draft"])
    g.add_edge("extract", "compare")
    g.add_edge("compare", "summarize_and_draft")
    g.add_conditional_edges("summarize_and_draft", _route_after_summary, ["human_review", END])
    g.add_conditional_edges("human_review", _route_after_human, ["security_precheck", "notify"])
    g.add_edge("notify", END)
    return g.compile(checkpointer=checkpointer or _make_checkpointer())


def _make_checkpointer():
    from app.config import ConfigurationError

    kind = os.environ.get("LANGGRAPH_CHECKPOINT", "memory").lower()
    if kind == "memory":
        return MemorySaver()
    if kind != "postgres":
        raise ConfigurationError("LANGGRAPH_CHECKPOINT must be memory or postgres")
    pg_url = os.environ.get("LANGGRAPH_PG_URL", "").strip()
    if not pg_url:
        raise ConfigurationError("LANGGRAPH_CHECKPOINT=postgres requires LANGGRAPH_PG_URL")
    try:
        from langgraph.checkpoint.postgres import PostgresSaver

        saver = _CHECKPOINTER_RESOURCES.enter_context(PostgresSaver.from_conn_string(pg_url))
        saver.setup()
        return saver
    except Exception as exc:
        raise ConfigurationError(f"Postgres LangGraph checkpointer initialization failed ({type(exc).__name__})") from exc


class CaseAgent:
    """Thin facade used by the API: run / state / resume."""

    def __init__(self, repo: BaseRepository) -> None:
        self.repo = repo
        self.graph = build_graph(repo)

    def _cfg(self, case_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": case_id}}

    def run(self, case_id: str, actor_id: str = "agent", mode: str = "single") -> dict[str, Any]:
        case = self.repo.get_case(case_id)
        if case is None:
            raise ValueError(f"unknown case {case_id}")
        init: GraphState = {"case_id": case_id, "email_id": case.source_email_id, "actor_id": actor_id, "trace": [], "errors": [], "rag_context": [], "notified": False}
        t0 = time.time()
        try:
            self.graph.invoke(init, config=self._cfg(case_id))
        except Exception as exc:
            self._record_run(case_id, actor_id, mode, t0, "AGENT_RUN", error=exc)
            raise
        st = self.state(case_id)
        st["agent_run"] = self._record_run(case_id, actor_id, mode, t0, "AGENT_RUN", paused=bool(st["paused"]))
        return st

    def resume(self, case_id: str, decision: dict[str, Any]) -> dict[str, Any]:
        actor_id = str(decision.get("user_id") or "agent")
        t0 = time.time()
        try:
            self.graph.invoke(Command(resume=decision), config=self._cfg(case_id))
        except Exception as exc:
            self._record_run(case_id, actor_id, "single", t0, "AGENT_RESUMED", error=exc, decision=str(decision.get("action") or ""))
            raise
        st = self.state(case_id)
        st["agent_run"] = self._record_run(case_id, actor_id, "single", t0, "AGENT_RESUMED", paused=bool(st["paused"]), decision=str(decision.get("action") or ""))
        return st

    def _record_run(self, case_id: str, actor_id: str, mode: str, t0: float, action: str, *, paused: bool = False,
                    error: Optional[Exception] = None, decision: Optional[str] = None) -> Optional[dict[str, Any]]:
        """Persist the outcome on the case (pending -> paused/completed/error) and audit it. Never raises."""
        try:
            case = self.repo.get_case(case_id)   # re-read: the nodes saved the case while the graph ran
            if case is None:
                return None
            prev = case.agent_run
            info = AgentRunInfo(
                runs=(prev.runs + 1) if prev else 1, last_run_at=datetime.utcnow(), last_run_by=actor_id,
                result="error" if error else ("paused" if paused else "completed"), status_after=case.status,
                ms=int((time.time() - t0) * 1000), mode=mode if mode in ("single", "batch") else "single",
                decision=decision or (prev.decision if prev and not error else None), error=type(error).__name__ if error else None,
            )
            case.agent_run = info
            self.repo.save_case(case)
            from app.pipeline.orchestrator import Pipeline

            Pipeline(self.repo).audit(case_id, ActorType.USER if actor_id not in ("agent", "scheduler") else ActorType.SYSTEM, actor_id, action, after=info.summary())
            return info.summary()
        except Exception:  # bookkeeping must never mask the run result
            return None

    def state(self, case_id: str) -> dict[str, Any]:
        snap = self.graph.get_state(self._cfg(case_id))
        values = dict(snap.values or {})
        interrupts = []
        for task in getattr(snap, "tasks", []) or []:
            for it in getattr(task, "interrupts", []) or []:
                interrupts.append(getattr(it, "value", it))
        return {
            "case_id": case_id,
            "paused": bool(snap.next) and bool(interrupts),
            "next": list(snap.next or []),
            "interrupt": interrupts[0] if interrupts else None,
            "route": values.get("route"), "status": values.get("status"), "needs_human": values.get("needs_human"), "notified": values.get("notified"),
            "security_agent": values.get("security_agent"), "rag_context": values.get("rag_context", []),
            "trace": values.get("trace", []), "human_decision": values.get("human_decision"),
        }

    def mermaid(self) -> str:
        try:
            return self.graph.get_graph().draw_mermaid()
        except Exception:
            return "graph TD; security_precheck-->security_agent-->classify-->detect_documents-->extract-->compare-->summarize_and_draft-->human_review-->notify"


@lru_cache(maxsize=1)
def get_agent() -> CaseAgent:
    from app.config import get_repo

    return CaseAgent(get_repo())
