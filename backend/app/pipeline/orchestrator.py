"""
LangGraph-style orchestration of the case pipeline.

  1 security precheck -> 2 intent classifier -> 3 attachment classifier
  -> 4 document extractor -> 5 normalization -> 6 DETERMINISTIC comparator
  -> 7 policy/recommendation -> 8 summary -> 9 draft -> 10 human approval gate
  -> 11 notifier (share/notify endpoints) -> 12 audit logger (every node)

Each node appends a DecisionTrace and audit events. Idempotent per email
(job key = email checksum): re-running never duplicates cases.
"""
from __future__ import annotations

import hashlib
import re
import time
import uuid
from datetime import datetime
from typing import Any, Optional

from app.ai.anomaly import detect_anomalies
from app.ai.assistant import detect_language
from app.ai.attachment_classifier import classify_attachment
from app.ai.extractor import extract_seven_fields
from app.ai.intent_classifier import classify_intent
from app.ai.security_precheck import assess_security
from app.ai.summary_draft import build_draft, build_summary
from app.contracts.schemas import (
    ActorType,
    AttachmentMeta,
    AuditEvent,
    CaseRecord,
    CaseStatus,
    ComparisonResult,
    DecisionTrace,
    DocumentType,
    DraftAction,
    EmailMessage,
    ErrorCategory,
    ExtractionStatus,
    HackathonCategory,
    Intent,
    ProcessingError,
    ReviewReason,
    SecurityOutcome,
    SevenFieldExtraction,
)
from app.core.comparator import compare_seven_fields
from app.core.policy import confidence_threshold, merged_policy
from app.core.recommendation import recommend
from app.readers.document_reader import read_document
from app.repositories.base import BaseRepository
from app.file_security import safe_attachment_name, validate_attachment_count, validate_file_size

VERIFICATION_INTENTS = {Intent.DOCUMENT_VERIFICATION, Intent.DOCUMENT_CORRECTION}


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


OCR_CONFIDENCE_CAP = 0.9  # extraction confidence ceiling for text recovered by OCR


class Pipeline:
    def __init__(self, repo: BaseRepository) -> None:
        self.repo = repo

    # ------------------------------------------------------------------ audit
    def audit(self, case_id: Optional[str], actor_type: ActorType, actor_id: str, action: str,
              before: Optional[dict] = None, after: Optional[dict] = None, evidence_ref: Optional[str] = None, policy_version: str = "v1") -> AuditEvent:
        ev = AuditEvent(event_id=_id("evt"), case_id=case_id, timestamp=datetime.utcnow(), actor_type=actor_type, actor_id=actor_id,
                        action=action, before=before, after=after, evidence_ref=evidence_ref, policy_version=policy_version)
        self.repo.append_audit(ev)
        return ev

    def _trace(self, case: CaseRecord, node: str, actor: ActorType, t0: float, output: dict[str, Any], policy_version: str) -> None:
        case.trace.append(DecisionTrace(node=node, actor_type=actor, started_at=datetime.utcfromtimestamp(t0), finished_at=datetime.utcnow(),
                                        output=output, policy_version=policy_version))

    def _set_status(self, case: CaseRecord, status: CaseStatus, actor_id: str = "pipeline", policy_version: str = "v1") -> None:
        before = case.status
        case.status = status
        if before != status:
            self.audit(case.id, ActorType.SYSTEM, actor_id, "STATUS_CHANGED", {"status": before.value}, {"status": status.value}, policy_version=policy_version)

    def _error(self, case: CaseRecord, category: ErrorCategory, step: str, message: str, recovery: str, retryable: bool = True) -> None:
        err = ProcessingError(id=_id("err"), case_id=case.id, category=category, step=step, message=message, recovery=recovery, retryable=retryable)
        case.errors.append(err)
        self.repo.save_error(err)
        self.audit(case.id, ActorType.SYSTEM, "pipeline", "ERROR", after={"category": category.value, "step": step, "message": message})

    # ------------------------------------------------------------- ingestion
    def ingest_email(self, raw: dict[str, Any], attachment_bytes: dict[str, bytes], provider: str = "bundle", received_at: Optional[datetime] = None) -> EmailMessage:
        """Persist an incoming message + attachment originals (never executed)."""
        eid = raw.get("email_id") or raw.get("id") or _id("email")
        body = raw.get("body", "") or ""
        subject = raw.get("subject", "") or ""
        checksum = hashlib.sha256(f"{raw.get('from','')}|{subject}|{body}".encode("utf-8")).hexdigest()
        dup = self.repo.find_email_by_checksum(checksum)
        atts: list[AttachmentMeta] = []
        attachment_paths = raw.get("attachments", []) or []
        validate_attachment_count(len(attachment_paths))
        for path in attachment_paths:
            name = safe_attachment_name(path)
            data = attachment_bytes.get(path, b"")
            validate_file_size(len(data))
            rr = read_document(name, data)
            pointer = f"{provider}/{eid}/{name}"
            self.repo.save_blob(pointer, data)
            dup_att = self.repo.find_attachment_by_checksum(rr.checksum) if data else None
            atts.append(AttachmentMeta(
                id=f"att_{eid}_{name.rsplit('.',1)[0].split('_')[-1]}_{rr.checksum[:8]}", source_email_id=eid, file_name=name, file_type=rr.file_type,
                size_bytes=rr.size_bytes, checksum=rr.checksum, storage_pointer=pointer, extraction_status=rr.status,
                raw_text=rr.text or None, page_count=rr.page_count, is_duplicate_of=dup_att,
                reader_note=rr.note, ocr=bool(rr.note and "OCR" in rr.note and rr.text),
            ))
        email = EmailMessage(
            id=eid, provider=provider, provider_message_id=raw.get("provider_message_id", eid), conversation_id=raw.get("conversation_id"),
            sender=raw.get("from", "") or "", sender_name=raw.get("from_name"), recipients=raw.get("to", []) or [], cc=raw.get("cc", []) or [],
            subject=subject, body=body, received_at=received_at or datetime.utcnow(), language=detect_language(body), attachments=atts,
            checksum=checksum, is_duplicate_of=(dup.id if dup and dup.id != eid else None),
            mailbox_user_id=raw.get("mailbox_user_id"), mailbox_address=raw.get("mailbox_address"),
        )
        self.repo.save_email(email)
        self.audit(None, ActorType.SYSTEM, "connector", "EMAIL_RECEIVED", after={"email_id": eid, "sender": email.sender, "subject": subject[:120], "attachments": len(atts),
                                                                                  **({"mailbox": email.mailbox_address} if email.mailbox_address else {})})
        return email

    # ------------------------------------------------------------- pipeline
    def run(self, email: EmailMessage, actor_id: str = "pipeline", force: bool = False) -> CaseRecord:
        job_key = f"run:{email.checksum}"
        existing = self.repo.get_case_by_email(email.id)
        if existing and not force and self.repo.seen_job(job_key):
            return existing  # idempotent replay

        t_start = time.time()
        policy_rec = self.repo.get_active_policy()
        policy = merged_policy(policy_rec.values)
        pv = policy_rec.version
        sec_policy = {**policy["security"]}

        # ---- node 1: security precheck
        t0 = time.time()
        security = assess_security(email, email.attachments, sec_policy)
        case = existing or CaseRecord(
            id=existing.id if existing else f"case_{email.id}", source_email_id=email.id, intent=Intent.UNKNOWN_REVIEW,
            hackathon_category=HackathonCategory.GENERAL, action_required=False, priority="LOW", status=CaseStatus.RECEIVED,
            security=security, classification=classify_intent(email, security, bool(email.attachments), policy["intent"]),
        )
        case.errors, case.trace, case.drafts, case.anomalies = [], [], [], []
        case.security = security
        self.audit(case.id, ActorType.SYSTEM, "pipeline", "CASE_CREATED" if not existing else "CASE_REPROCESSED", after={"email_id": email.id}, policy_version=pv)
        self._set_status(case, CaseStatus.SECURITY_CHECK, policy_version=pv)
        self._trace(case, "security_precheck", ActorType.AI, t0, {"outcome": security.outcome.value, "score": security.score, "signals": [s.signal for s in security.signals]}, pv)
        self.audit(case.id, ActorType.AI, "security_precheck", "SECURITY_CLASSIFIED", after={"outcome": security.outcome.value, "score": security.score}, policy_version=pv)

        # ---- node 2: intent
        t0 = time.time()
        cls = classify_intent(email, security, bool(email.attachments), policy["intent"])
        case.classification, case.intent, case.hackathon_category = cls, cls.intent, cls.hackathon_category
        case.action_required, case.priority, case.confidence = cls.action_required, cls.priority, cls.confidence
        self._trace(case, "intent_classifier", ActorType.AI, t0, {"intent": cls.intent.value, "category": cls.hackathon_category.value, "confidence": cls.confidence, "decided_by": cls.decided_by, "rationale": cls.rationale}, pv)
        self.audit(case.id, ActorType.AI, "intent_classifier", "INTENT_CLASSIFIED", after={"intent": cls.intent.value, "confidence": cls.confidence, "rationale": cls.rationale}, policy_version=pv)

        if security.outcome == SecurityOutcome.SECURITY_REVIEW:
            self._set_status(case, CaseStatus.SECURITY_REVIEW, policy_version=pv)
            return self._finish(case, email, None, None, t_start, pv, policy)
        self._set_status(case, CaseStatus.CLASSIFIED, policy_version=pv)

        if cls.intent not in VERIFICATION_INTENTS:
            # informational / SI request / invoice / spam -> no comparison
            self._set_status(case, CaseStatus.NO_ACTION_INFO if not cls.action_required else CaseStatus.CLASSIFIED, policy_version=pv)
            return self._finish(case, email, None, None, t_start, pv, policy)

        # ---- node 3: attachment classifier
        t0 = time.time()
        si_att: Optional[AttachmentMeta] = None
        bl_att: Optional[AttachmentMeta] = None
        wrong_types: list[str] = []
        for a in email.attachments:
            ac = classify_attachment(a, a.raw_text)
            a.detected_type, a.detection_confidence = ac.detected_type, ac.confidence
            self.audit(case.id, ActorType.AI, "attachment_classifier", "ATTACHMENT_CLASSIFIED", after={"attachment": a.file_name, "type": ac.detected_type.value, "confidence": ac.confidence, "rationale": ac.rationale}, policy_version=pv)
            if ac.detected_type == DocumentType.SHIPPING_INSTRUCTION and si_att is None:
                si_att = a
            elif ac.detected_type == DocumentType.DRAFT_BL and bl_att is None:
                bl_att = a
            elif ac.detected_type in (DocumentType.INVOICE, DocumentType.SUPPORTING_DOCUMENT):
                wrong_types.append(a.file_name)
            elif ac.detected_type == DocumentType.UNKNOWN_DOCUMENT and a.extraction_status != ExtractionStatus.EXTRACTED:
                # unreadable file: infer role from filename so we can report *which* doc is unreadable
                if "_SI" in a.file_name.upper() and si_att is None:
                    si_att = a
                elif "_BL" in a.file_name.upper() and bl_att is None:
                    bl_att = a
        self.repo.save_email(email)
        case.si_available = si_att is not None and si_att.extraction_status == ExtractionStatus.EXTRACTED
        case.bl_available = bl_att is not None and bl_att.extraction_status == ExtractionStatus.EXTRACTED
        case.si_document_id = si_att.id if si_att else None
        case.bl_document_id = bl_att.id if bl_att else None
        self._trace(case, "attachment_classifier", ActorType.AI, t0, {"si": si_att.file_name if si_att else None, "bl": bl_att.file_name if bl_att else None, "wrong_types": wrong_types}, pv)
        self._set_status(case, CaseStatus.DOCUMENTS_DETECTED if email.attachments else CaseStatus.WAITING_DOCUMENTS, policy_version=pv)

        review_reason: Optional[ReviewReason] = None
        if si_att is None or bl_att is None:
            top = re.split(r"\n_{5,}|\nBest Regards,|\nRegards,", email.body, maxsplit=1)[0]
            claims_docs = bool(re.search(r"attached|find attached|please compare|compare the si|kindly confirm the bl|missing|dropped", top, re.I))
            if not email.attachments and not claims_docs:
                # A plain "please send the draft BL" request: documents are expected later - not an escalation.
                case.review_reason = None
                self._set_status(case, CaseStatus.WAITING_DOCUMENTS, policy_version=pv)
                return self._finish(case, email, None, None, t_start, pv, policy, draft_bl_requested=True)
            if wrong_types and si_att is not None:
                review_reason = ReviewReason.WRONG_DOC_TYPE
                self._error(case, ErrorCategory.MISSING_BL, "attachment_classifier", f"Attachment(s) {', '.join(wrong_types)} are not a Draft BL.", "Request the correct Draft BL from the sender, then Retry.")
            else:
                review_reason = ReviewReason.MISSING_ATTACHMENT
                if si_att is None:
                    self._error(case, ErrorCategory.MISSING_SI, "attachment_classifier", "No Shipping Instruction attached.", "Upload/link the SI, then Retry.")
                if bl_att is None:
                    self._error(case, ErrorCategory.MISSING_BL, "attachment_classifier", "No Draft BL attached.", "Upload/link the Draft BL, then Retry.")
            self._set_status(case, CaseStatus.WAITING_DOCUMENTS, policy_version=pv)
            case.review_reason = review_reason
            return self._finish(case, email, None, None, t_start, pv, policy)

        unreadable = [a for a in (si_att, bl_att) if a.extraction_status != ExtractionStatus.EXTRACTED]
        if unreadable:
            for a in unreadable:
                cat = ErrorCategory.UNSUPPORTED_FILE if a.extraction_status == ExtractionStatus.UNSUPPORTED else ErrorCategory.OCR_ERROR
                self._error(case, cat, "document_reader", f"'{a.file_name}' is {a.extraction_status.value.lower()}.", "Request a text-readable copy or run OCR, then Retry.")
            case.review_reason = ReviewReason.UNREADABLE
            self._set_status(case, CaseStatus.HUMAN_REVIEW, policy_version=pv)
            return self._finish(case, email, None, None, t_start, pv, policy)

        # ---- node 4+5: extraction + normalization
        t0 = time.time()
        self._set_status(case, CaseStatus.EXTRACTING, policy_version=pv)
        si_x = extract_seven_fields(si_att.raw_text or "", si_att.id)
        bl_x = extract_seven_fields(bl_att.raw_text or "", bl_att.id)
        si_att.extraction_confidence, bl_att.extraction_confidence = si_x.overall_confidence(), bl_x.overall_confidence()
        for att in (si_att, bl_att):  # OCR provenance is visible in the confidence: capped, never boosted
            if att.ocr:
                att.extraction_confidence = min(att.extraction_confidence, OCR_CONFIDENCE_CAP)
        case.si_extraction, case.bl_extraction = si_x, bl_x
        self.repo.save_email(email)
        self._trace(case, "document_extractor", ActorType.AI, t0, {"si_confidence": si_x.overall_confidence(), "bl_confidence": bl_x.overall_confidence()}, pv)
        self.audit(case.id, ActorType.AI, "document_extractor", "EXTRACTION_COMPLETED", after={"si_confidence": si_x.overall_confidence(), "bl_confidence": bl_x.overall_confidence()}, evidence_ref=f"{si_att.id},{bl_att.id}", policy_version=pv)

        # ---- node 6: deterministic comparison
        t0 = time.time()
        self._set_status(case, CaseStatus.COMPARING, policy_version=pv)
        self.audit(case.id, ActorType.SYSTEM, "comparator", "COMPARISON_STARTED", policy_version=pv)
        cmp = compare_seven_fields(si_x, bl_x, confidence_threshold=confidence_threshold(policy), si_document_id=si_att.id, bl_document_id=bl_att.id, policy_version=pv)
        case.comparison, case.mismatch_count, case.comparison_status = cmp, cmp.mismatch_count, cmp.comparison_status
        case.review_reason = cmp.review_reason
        case.confidence = round(min(f.confidence for f in cmp.fields), 3)
        for f in cmp.fields:
            self.audit(case.id, ActorType.SYSTEM, "comparator", "FIELD_RESULT", after={"field": f.field, "result": f.result.value, "si": f.si_original, "bl": f.bl_original, "confidence": f.confidence}, policy_version=pv)
        self._trace(case, "seven_field_comparator", ActorType.SYSTEM, t0, {"status": cmp.comparison_status.value, "mismatch_count": cmp.mismatch_count, "mismatch_fields": cmp.mismatch_fields, "review_fields": cmp.review_fields, "message": cmp.message}, pv)
        if cmp.mismatch_count > 0:
            self.audit(case.id, ActorType.SYSTEM, "comparator", "MISMATCH_DETECTED", after={"count": cmp.mismatch_count, "fields": cmp.mismatch_fields}, policy_version=pv)
            self._set_status(case, CaseStatus.MISMATCH_DETECTED, policy_version=pv)
        elif cmp.review_fields:
            self._set_status(case, CaseStatus.HUMAN_REVIEW, policy_version=pv)
        else:
            self._set_status(case, CaseStatus.NO_MISMATCH_DETECTED, policy_version=pv)
        return self._finish(case, email, cmp, case.review_reason, t_start, pv, policy)

    # ------------------------------------------------------------- finish
    def _finish(self, case: CaseRecord, email: EmailMessage, cmp: Optional[ComparisonResult], review_reason: Optional[ReviewReason], t_start: float, pv: str, policy: dict, draft_bl_requested: bool = False) -> CaseRecord:
        # node 7: policy evaluator + recommendation
        t0 = time.time()
        rec = recommend(case.classification, case.security, cmp, review_reason, case.si_available, case.bl_available, draft_bl_requested=draft_bl_requested)
        case.recommendation = rec
        case.action_required = rec.action_required
        case.priority = rec.priority if rec.priority.value != "LOW" or not case.classification.action_required else case.priority
        self._trace(case, "policy_evaluator", ActorType.SYSTEM, t0, {"action_type": rec.action_type.value, "priority": rec.priority.value, "responsible_role": rec.responsible_role.value}, pv)
        self.audit(case.id, ActorType.SYSTEM, "policy_evaluator", "POLICY_APPLIED", after={"action_type": rec.action_type.value, "policy_version": pv}, policy_version=pv)

        # node 8: summary
        t0 = time.time()
        case.summary = build_summary(email, case.classification, case.security, cmp, review_reason, case.si_available, case.bl_available, draft_bl_requested=draft_bl_requested)
        self._trace(case, "summary_generator", ActorType.AI, t0, {"text": case.summary.text}, pv)

        # node 9: draft (never auto-sent)
        t0 = time.time()
        draft = build_draft(email, case.classification, cmp, review_reason, case.si_available, case.bl_available, draft_bl_requested=draft_bl_requested)
        if draft:
            draft.id = _id("draft")
            case.drafts.append(draft)
            self.audit(case.id, ActorType.AI, "draft_generator", "DRAFT_GENERATED", after={"draft_id": draft.id, "type": draft.draft_type, "to": draft.to}, policy_version=pv)
            self._trace(case, "draft_generator", ActorType.AI, t0, {"draft_type": draft.draft_type, "to": draft.to}, pv)
            if case.status in (CaseStatus.MISMATCH_DETECTED, CaseStatus.NO_MISMATCH_DETECTED):
                self._set_status(case, CaseStatus.DRAFT_READY, policy_version=pv)

        # anomalies
        case.anomalies = detect_anomalies(email, email.attachments, cmp)
        for a in case.anomalies:
            self.audit(case.id, ActorType.AI, "anomaly_detector", "UNUSUAL_BEHAVIOUR", after={"signal": a.signal, "severity": a.severity, "evidence": a.evidence}, policy_version=pv)

        # node 10: human approval gate — MISMATCH/HUMAN_REVIEW cases wait for a human
        if case.status == CaseStatus.DRAFT_READY and cmp and cmp.mismatch_count > 0:
            self._set_status(case, CaseStatus.HUMAN_REVIEW, policy_version=pv)
        if case.status == CaseStatus.CLASSIFIED and not case.action_required:
            self._set_status(case, CaseStatus.NO_ACTION_INFO, policy_version=pv)

        case.processing_ms = int((time.time() - t_start) * 1000)
        self.repo.save_case(case)
        self.repo.mark_job(f"run:{email.checksum}", {"case_id": case.id, "status": case.status.value})
        return case
