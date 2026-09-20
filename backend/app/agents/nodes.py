"""
`Nodes` - one method per graph node (mirrors `src/nodes.py` in kaymen99/langgraph-email-automation).

Every node:
  * reads the case + email through the repository (backend owns persistence),
  * calls the same deterministic modules the classic Pipeline uses,
  * writes only its own keys into GraphState,
  * appends a trace entry + audit event.

The human_review node calls `interrupt()` - the graph pauses (checkpointed) until
`/agent/resume/{case_id}` supplies a HumanDecision.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from langgraph.types import interrupt

from app.agents import prompts
from app.agents.rag import get_rag
from app.agents.state import GraphState
from app.ai.anomaly import detect_anomalies
from app.ai.attachment_classifier import classify_attachment
from app.ai.extractor import extract_seven_fields
from app.ai.intent_classifier import classify_intent
from app.ai.llm import get_llm
from app.ai.security_precheck import assess_security
from app.ai.summary_draft import build_draft, build_summary, polish_with_llm
from app.contracts.schemas import (
    ActorType,
    CaseRecord,
    CaseStatus,
    DocumentType,
    ExtractionStatus,
    HackathonCategory,
    Intent,
    ReviewReason,
    SecurityOutcome,
)
from app.core.comparator import compare_seven_fields
from app.core.policy import confidence_threshold, merged_policy
from app.core.recommendation import recommend
from app.pipeline.orchestrator import Pipeline, _id
from app.repositories.base import BaseRepository

VERIFICATION_INTENTS = {Intent.DOCUMENT_VERIFICATION, Intent.DOCUMENT_CORRECTION}


class Nodes:
    def __init__(self, repo: BaseRepository) -> None:
        self.repo = repo
        self.pipe = Pipeline(repo)

    # ------------------------------------------------------------ helpers
    def _case(self, state: GraphState) -> CaseRecord:
        case = self.repo.get_case(state["case_id"])
        if case is None:
            raise RuntimeError(f"case {state['case_id']} not found")
        return case

    def _email(self, case: CaseRecord):
        return self.repo.get_email(case.source_email_id)

    def _policy(self):
        rec = self.repo.get_active_policy()
        return merged_policy(rec.values), rec.version

    def _trace(self, state: GraphState, node: str, actor: ActorType, t0: float, output: dict[str, Any]) -> list[dict[str, Any]]:
        entry = {"node": node, "actor_type": actor.value, "started_at": datetime.utcfromtimestamp(t0).isoformat(), "finished_at": datetime.utcnow().isoformat(), "output": output}
        return [*state.get("trace", []), entry]

    def _set_status(self, case: CaseRecord, status: CaseStatus, actor_id: str = "agent") -> None:
        if case.status != status:
            self.pipe.audit(case.id, ActorType.SYSTEM, actor_id, "STATUS_CHANGED", {"status": case.status.value}, {"status": status.value})
            case.status = status
        self.repo.save_case(case)

    # ------------------------------------------------------------ 1. security precheck (deterministic)
    def security_precheck(self, state: GraphState) -> dict[str, Any]:
        t0 = time.time()
        case = self._case(state)
        email = self._email(case)
        policy, _ = self._policy()
        sec = assess_security(email, email.attachments, policy["security"])
        case.security = sec
        self._set_status(case, CaseStatus.SECURITY_CHECK)
        self.pipe.audit(case.id, ActorType.AI, "security_precheck", "SECURITY_CLASSIFIED", after={"outcome": sec.outcome.value, "score": sec.score})
        return {"security": sec.model_dump(mode="json"), "trace": self._trace(state, "security_precheck", ActorType.AI, t0, {"outcome": sec.outcome.value, "score": sec.score})}

    # ------------------------------------------------------------ 2. SECURITY AGENT (LLM reasoning over the signals, rules as fallback)
    def security_agent(self, state: GraphState) -> dict[str, Any]:
        t0 = time.time()
        case = self._case(state)
        email = self._email(case)
        sec = state["security"]
        verdict = {"outcome": sec["outcome"], "confidence": round(1 - abs(0.5 - sec["score"]) * 0.5, 2), "reasoning": sec["rationale"], "recommended_action": (sec["signals"][0]["recommended_action"] if sec["signals"] else "None"), "decided_by": "rule"}
        llm = get_llm()
        if llm.enabled:
            payload = f"Precheck: {sec}\n\nFrom: {email.sender}\nSubject: {email.subject}\nBody:\n{email.body[:1500]}\nAttachments: {[a.file_name for a in email.attachments]}"
            data = llm.complete_json(prompts.SECURITY_AGENT_PROMPT, payload, max_tokens=300, purpose="security", case_id=state.get("case_id"))
            if data and str(data.get("outcome", "")).upper() in SecurityOutcome.__members__:
                # The agent may only ESCALATE (never downgrade a rule-based SPAM/SECURITY_REVIEW to SAFE)
                order = ["SAFE", "SUSPICIOUS", "SPAM", "SECURITY_REVIEW"]
                new = str(data["outcome"]).upper()
                if order.index(new) >= order.index(sec["outcome"]):
                    verdict = {"outcome": new, "confidence": float(data.get("confidence", 0.7)), "reasoning": str(data.get("reasoning", ""))[:400], "recommended_action": str(data.get("recommended_action", ""))[:200], "decided_by": "llm"}
                    case.security.outcome = SecurityOutcome(new)
        route = "security_review" if verdict["outcome"] == "SECURITY_REVIEW" else ("no_action" if verdict["outcome"] == "SPAM" else "continue")
        if route == "security_review":
            self._set_status(case, CaseStatus.SECURITY_REVIEW)
        else:
            self.repo.save_case(case)
        self.pipe.audit(case.id, ActorType.AI, "security_agent", "SECURITY_AGENT_VERDICT", after=verdict)
        return {"security_agent": verdict, "route": route, "trace": self._trace(state, "security_agent", ActorType.AI, t0, verdict)}

    # ------------------------------------------------------------ 3. intent
    def classify(self, state: GraphState) -> dict[str, Any]:
        t0 = time.time()
        case = self._case(state)
        email = self._email(case)
        policy, _ = self._policy()
        cls = classify_intent(email, case.security, bool(email.attachments), policy["intent"])
        case.classification, case.intent, case.hackathon_category = cls, cls.intent, cls.hackathon_category
        case.action_required, case.priority, case.confidence = cls.action_required, cls.priority, cls.confidence
        self._set_status(case, CaseStatus.CLASSIFIED)
        self.pipe.audit(case.id, ActorType.AI, "intent_classifier", "INTENT_CLASSIFIED", after={"intent": cls.intent.value, "confidence": cls.confidence, "rationale": cls.rationale})
        route = "verification" if cls.intent in VERIFICATION_INTENTS else ("no_action" if not cls.action_required else "other")
        return {"classification": cls.model_dump(mode="json"), "route": route, "status": case.status.value,
                "trace": self._trace(state, "intent_classifier", ActorType.AI, t0, {"intent": cls.intent.value, "category": cls.hackathon_category.value, "confidence": cls.confidence})}

    # ------------------------------------------------------------ 4. attachments
    def detect_documents(self, state: GraphState) -> dict[str, Any]:
        t0 = time.time()
        case = self._case(state)
        email = self._email(case)
        si = bl = None
        wrong: list[str] = []
        out = []
        for a in email.attachments:
            ac = classify_attachment(a, a.raw_text)
            a.detected_type, a.detection_confidence = ac.detected_type, ac.confidence
            out.append(ac.model_dump(mode="json"))
            self.pipe.audit(case.id, ActorType.AI, "attachment_classifier", "ATTACHMENT_CLASSIFIED", after={"attachment": a.file_name, "type": ac.detected_type.value, "confidence": ac.confidence})
            if ac.detected_type == DocumentType.SHIPPING_INSTRUCTION and si is None:
                si = a
            elif ac.detected_type == DocumentType.DRAFT_BL and bl is None:
                bl = a
            elif ac.detected_type in (DocumentType.INVOICE, DocumentType.SUPPORTING_DOCUMENT):
                wrong.append(a.file_name)
            elif ac.detected_type == DocumentType.UNKNOWN_DOCUMENT and a.extraction_status != ExtractionStatus.EXTRACTED:
                if "_SI" in a.file_name.upper() and si is None:
                    si = a
                elif "_BL" in a.file_name.upper() and bl is None:
                    bl = a
        self.repo.save_email(email)
        case.si_available = bool(si and si.extraction_status == ExtractionStatus.EXTRACTED)
        case.bl_available = bool(bl and bl.extraction_status == ExtractionStatus.EXTRACTED)
        case.si_document_id, case.bl_document_id = (si.id if si else None), (bl.id if bl else None)
        review = None
        route = "extract"
        import re

        top = re.split(r"\n_{5,}|\nBest Regards,|\nRegards,", email.body, maxsplit=1)[0]
        claims_docs = bool(re.search(r"attached|find attached|please compare|compare the si|kindly confirm the bl|missing|dropped", top, re.I))
        if si is None or bl is None:
            if not email.attachments and not claims_docs:
                route = "waiting_documents"  # plain "please send the draft BL"
            elif wrong and si is not None:
                review, route = ReviewReason.WRONG_DOC_TYPE, "waiting_documents"
            else:
                review, route = ReviewReason.MISSING_ATTACHMENT, "waiting_documents"
        elif any(a.extraction_status != ExtractionStatus.EXTRACTED for a in (si, bl)):
            review, route = ReviewReason.UNREADABLE, "human_review"
        case.review_reason = review
        self._set_status(case, CaseStatus.DOCUMENTS_DETECTED if email.attachments else CaseStatus.WAITING_DOCUMENTS)
        return {"attachments": out, "review_reason": review.value if review else None, "route": route,
                "trace": self._trace(state, "attachment_classifier", ActorType.AI, t0, {"si": si.file_name if si else None, "bl": bl.file_name if bl else None, "wrong_types": wrong, "route": route})}

    # ------------------------------------------------------------ 5. extraction + normalization
    def extract(self, state: GraphState) -> dict[str, Any]:
        t0 = time.time()
        case = self._case(state)
        email = self._email(case)
        si = next(a for a in email.attachments if a.id == case.si_document_id)
        bl = next(a for a in email.attachments if a.id == case.bl_document_id)
        self._set_status(case, CaseStatus.EXTRACTING)
        six, blx = extract_seven_fields(si.raw_text or "", si.id), extract_seven_fields(bl.raw_text or "", bl.id)
        case.si_extraction, case.bl_extraction = six, blx
        si.extraction_confidence, bl.extraction_confidence = six.overall_confidence(), blx.overall_confidence()
        self.repo.save_email(email)
        self.repo.save_case(case)
        self.pipe.audit(case.id, ActorType.AI, "document_extractor", "EXTRACTION_COMPLETED", after={"si_confidence": six.overall_confidence(), "bl_confidence": blx.overall_confidence()}, evidence_ref=f"{si.id},{bl.id}")
        return {"si_extraction": six.model_dump(mode="json"), "bl_extraction": blx.model_dump(mode="json"),
                "trace": self._trace(state, "document_extractor", ActorType.AI, t0, {"si_confidence": six.overall_confidence(), "bl_confidence": blx.overall_confidence()})}

    # ------------------------------------------------------------ 6. DETERMINISTIC comparator (tool, no LLM)
    def compare(self, state: GraphState) -> dict[str, Any]:
        t0 = time.time()
        case = self._case(state)
        policy, pv = self._policy()
        self._set_status(case, CaseStatus.COMPARING)
        cmp = compare_seven_fields(case.si_extraction, case.bl_extraction, confidence_threshold=confidence_threshold(policy), si_document_id=case.si_document_id, bl_document_id=case.bl_document_id, policy_version=pv)
        case.comparison, case.mismatch_count, case.comparison_status, case.review_reason = cmp, cmp.mismatch_count, cmp.comparison_status, cmp.review_reason
        case.confidence = round(min(f.confidence for f in cmp.fields), 3)
        for f in cmp.fields:
            self.pipe.audit(case.id, ActorType.SYSTEM, "comparator", "FIELD_RESULT", after={"field": f.field, "result": f.result.value, "si": f.si_original, "bl": f.bl_original, "confidence": f.confidence}, policy_version=pv)
        if cmp.mismatch_count > 0:
            self.pipe.audit(case.id, ActorType.SYSTEM, "comparator", "MISMATCH_DETECTED", after={"count": cmp.mismatch_count, "fields": cmp.mismatch_fields}, policy_version=pv)
            self._set_status(case, CaseStatus.MISMATCH_DETECTED)
        elif cmp.review_fields:
            self._set_status(case, CaseStatus.HUMAN_REVIEW)
        else:
            self._set_status(case, CaseStatus.NO_MISMATCH_DETECTED)
        return {"comparison": cmp.model_dump(mode="json"), "review_reason": cmp.review_reason.value if cmp.review_reason else None, "status": case.status.value,
                "trace": self._trace(state, "seven_field_comparator", ActorType.SYSTEM, t0, {"status": cmp.comparison_status.value, "mismatch_count": cmp.mismatch_count, "mismatch_fields": cmp.mismatch_fields, "review_fields": cmp.review_fields, "message": cmp.message})}

    # ------------------------------------------------------------ 7-9. policy + summary + draft (+ RAG context)
    def summarize_and_draft(self, state: GraphState) -> dict[str, Any]:
        t0 = time.time()
        case = self._case(state)
        email = self._email(case)
        _, pv = self._policy()
        draft_bl_requested = state.get("route") == "waiting_documents" and case.review_reason is None and not email.attachments
        rec = recommend(case.classification, case.security, case.comparison, case.review_reason, case.si_available, case.bl_available, draft_bl_requested=draft_bl_requested)
        case.recommendation, case.action_required = rec, rec.action_required
        case.summary = build_summary(email, case.classification, case.security, case.comparison, case.review_reason, case.si_available, case.bl_available, draft_bl_requested=draft_bl_requested)
        draft = build_draft(email, case.classification, case.comparison, case.review_reason, case.si_available, case.bl_available, draft_bl_requested=draft_bl_requested)
        if draft:
            draft = polish_with_llm(draft, case.comparison)
            draft.id = _id("draft")
            draft.version = len(case.drafts) + 1
            case.drafts.append(draft)
            self.pipe.audit(case.id, ActorType.AI, "draft_generator", "DRAFT_GENERATED", after={"draft_id": draft.id, "type": draft.draft_type, "to": draft.to})
        case.anomalies = detect_anomalies(email, email.attachments, case.comparison)
        rag_ctx = []
        try:
            rag_ctx = get_rag().search(f"{email.subject} {case.summary.text}", case_id=case.id, k=4, sources=["policy", "glossary", "ports"])
        except Exception:
            pass
        needs_human = bool(case.comparison and (case.comparison.mismatch_count > 0 or case.comparison.review_fields)) or case.review_reason == ReviewReason.UNREADABLE
        if case.status in (CaseStatus.MISMATCH_DETECTED, CaseStatus.NO_MISMATCH_DETECTED) and draft:
            self._set_status(case, CaseStatus.DRAFT_READY)
        if needs_human and case.status != CaseStatus.HUMAN_REVIEW:
            self._set_status(case, CaseStatus.HUMAN_REVIEW)
        if case.status in (CaseStatus.CLASSIFIED, CaseStatus.SECURITY_CHECK) and not case.action_required:
            self._set_status(case, CaseStatus.NO_ACTION_INFO)
        elif case.status == CaseStatus.SECURITY_CHECK:
            self._set_status(case, CaseStatus.CLASSIFIED)
        self.pipe.audit(case.id, ActorType.SYSTEM, "policy_evaluator", "POLICY_APPLIED", after={"action_type": rec.action_type.value, "policy_version": pv}, policy_version=pv)
        self.repo.save_case(case)
        return {"recommendation": rec.model_dump(mode="json"), "summary": case.summary.model_dump(mode="json"), "draft": draft.model_dump(mode="json") if draft else None,
                "rag_context": [{"id": h["id"], "source": h["source"], "score": h["score"]} for h in rag_ctx], "needs_human": needs_human, "status": case.status.value,
                "trace": self._trace(state, "summary_and_draft", ActorType.AI, t0, {"action_type": rec.action_type.value, "draft": draft.draft_type if draft else None, "needs_human": needs_human})}

    # ------------------------------------------------------------ 10. HUMAN-IN-THE-LOOP gate (interrupt)
    def human_review(self, state: GraphState) -> dict[str, Any]:
        """Pauses the graph. Resumed via Command(resume=HumanDecision) from POST /agent/resume/{case_id}."""
        case = self._case(state)
        decision: dict[str, Any] = interrupt({
            "case_id": case.id, "status": case.status.value, "summary": case.summary.text if case.summary else "",
            "mismatch_fields": case.comparison.mismatch_fields if case.comparison else [], "review_reason": case.review_reason.value if case.review_reason else None,
            "draft_id": case.drafts[-1].id if case.drafts else None, "draft_subject": case.drafts[-1].subject if case.drafts else None,
            "allowed_actions": ["approve", "edit", "reject", "reassign", "notify_party", "retry", "mark_no_action", "complete"],
        })
        t0 = time.time()
        from app.contracts.schemas import AssignRequest, DraftDecision, ShareRequest
        from app.services.case_service import CaseService

        svc = CaseService(self.repo)
        user = self.repo.get_user(decision.get("user_id", state.get("actor_id", "u_sup_1"))) or self.repo.get_user("u_sup_1")
        action = decision.get("action", "reject")
        result: dict[str, Any] = {"action": action, "by": user.id}
        try:
            if action == "approve" and decision.get("draft_id"):
                svc.approve_draft(case.id, DraftDecision(draft_id=decision["draft_id"], edited_body=decision.get("edited_body"), edited_subject=decision.get("edited_subject"), note=decision.get("note")), user)
            elif action == "edit" and decision.get("draft_id"):
                svc.edit_draft(case.id, DraftDecision(draft_id=decision["draft_id"], edited_body=decision.get("edited_body"), edited_subject=decision.get("edited_subject"), note=decision.get("note")), user)
            elif action == "reject" and decision.get("draft_id"):
                svc.reject_draft(case.id, DraftDecision(draft_id=decision["draft_id"], note=decision.get("note")), user)
            elif action == "reassign":
                svc.assign(case.id, AssignRequest(user_id=(decision.get("recipient") or {}).get("recipient_user_id"), note=decision.get("note")), user)
            elif action == "notify_party" and decision.get("recipient"):
                r = decision["recipient"]
                svc.begin_notify_party(case.id, user)
                result["share"] = svc.share(case.id, ShareRequest(**{**r, "confirm_external": bool(r.get("confirm_external", False))}), user)
            elif action == "mark_no_action":
                svc.mark_no_action(case.id, user)
            elif action == "complete":
                svc.complete(case.id, user, note=decision.get("note"))
            elif action == "retry":
                result["retry"] = True
        except Exception as exc:  # surfaced, never swallowed silently
            result["error"] = f"{type(exc).__name__}: {getattr(exc, 'detail', str(exc))}"
            self.pipe.audit(case.id, ActorType.SYSTEM, "human_review", "ERROR", after={"category": "AUTH_ERROR" if "403" in str(exc) else "COMPARISON_ERROR", "message": result["error"]})
        self.pipe.audit(case.id, ActorType.USER, user.id, "HUMAN_DECISION", after={k: v for k, v in decision.items() if k != "edited_body"})
        case = self._case(state)
        return {"human_decision": decision, "status": case.status.value, "route": "retry" if action == "retry" else "notify",
                "trace": self._trace(state, "human_review", ActorType.USER, t0, result)}

    # ------------------------------------------------------------ 11. notifier + 12. audit
    def notify(self, state: GraphState) -> dict[str, Any]:
        t0 = time.time()
        case = self._case(state)
        decision = state.get("human_decision") or {}
        notified = decision.get("action") in ("approve", "notify_party")
        self.pipe.audit(case.id, ActorType.SYSTEM, "agent_graph", "GRAPH_COMPLETED", after={"status": case.status.value, "notified": notified, "nodes": [t["node"] for t in state.get("trace", [])]})
        self.repo.mark_job(f"graph:{case.id}", {"status": case.status.value, "notified": notified})
        return {"notified": notified, "status": case.status.value, "trace": self._trace(state, "notifier", ActorType.SYSTEM, t0, {"notified": notified})}
