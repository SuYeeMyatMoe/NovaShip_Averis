"""
FROZEN CONTRACTS - NovaShip Averis
===================================
Every layer (AI, backend, frontend, assistant) codes against these shapes.
Do not change field names or enum literals without a team-wide contract bump
(see docs/CONTRACTS.md). Add fields; never rename or remove.

The seven-field comparison is deterministic (core/comparator.py). AI may
classify, extract, summarise and draft - it never decides MATCH/MISMATCH.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# The seven baseline fields (order matters for display)
# ---------------------------------------------------------------------------
SEVEN_FIELDS: tuple[str, ...] = (
    "shipper",
    "consignee",
    "notify_party",
    "port_of_loading",
    "port_of_discharge",
    "container_count",
    "gross_weight_kg",
)

FIELD_LABELS: dict[str, str] = {
    "shipper": "Shipper",
    "consignee": "Consignee",
    "notify_party": "Notify Party",
    "port_of_loading": "Port of Loading",
    "port_of_discharge": "Port of Discharge",
    "container_count": "Container Count",
    "gross_weight_kg": "Gross Weight (kg)",
}

NO_MISMATCH_MESSAGE = "No mismatch detected."


# ---------------------------------------------------------------------------
# Enumerations (machine-readable literals)
# ---------------------------------------------------------------------------
class SecurityOutcome(str, Enum):
    SAFE = "SAFE"
    SPAM = "SPAM"
    SUSPICIOUS = "SUSPICIOUS"
    SECURITY_REVIEW = "SECURITY_REVIEW"


class Intent(str, Enum):
    DOCUMENT_VERIFICATION = "DOCUMENT_VERIFICATION"
    PREPARE_SHIPPING_INSTRUCTION = "PREPARE_SHIPPING_INSTRUCTION"
    INVOICE_QUERY = "INVOICE_QUERY"
    DOCUMENT_CORRECTION = "DOCUMENT_CORRECTION"
    OPERATIONAL_UPDATE = "OPERATIONAL_UPDATE"
    GENERAL_ENQUIRY = "GENERAL_ENQUIRY"
    INFORMATION_ONLY = "INFORMATION_ONLY"
    NO_ACTION_REQUIRED = "NO_ACTION_REQUIRED"
    UNKNOWN_REVIEW = "UNKNOWN_REVIEW"


# Hackathon scoring categories (external contract with the SDOC bundle)
class HackathonCategory(str, Enum):
    BL_COMPARISON = "BL_COMPARISON"
    SI_REQUEST = "SI_REQUEST"
    INVOICE_QUERY = "INVOICE_QUERY"
    GENERAL = "GENERAL"
    SPAM = "SPAM"


INTENT_TO_CATEGORY: dict[Intent, HackathonCategory] = {
    Intent.DOCUMENT_VERIFICATION: HackathonCategory.BL_COMPARISON,
    Intent.DOCUMENT_CORRECTION: HackathonCategory.BL_COMPARISON,
    Intent.PREPARE_SHIPPING_INSTRUCTION: HackathonCategory.SI_REQUEST,
    Intent.INVOICE_QUERY: HackathonCategory.INVOICE_QUERY,
    Intent.OPERATIONAL_UPDATE: HackathonCategory.GENERAL,
    Intent.GENERAL_ENQUIRY: HackathonCategory.GENERAL,
    Intent.INFORMATION_ONLY: HackathonCategory.GENERAL,
    Intent.NO_ACTION_REQUIRED: HackathonCategory.GENERAL,
    Intent.UNKNOWN_REVIEW: HackathonCategory.GENERAL,
}


class Priority(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class DocumentType(str, Enum):
    SHIPPING_INSTRUCTION = "SHIPPING_INSTRUCTION"
    DRAFT_BL = "DRAFT_BL"
    INVOICE = "INVOICE"
    SUPPORTING_DOCUMENT = "SUPPORTING_DOCUMENT"
    UNKNOWN_DOCUMENT = "UNKNOWN_DOCUMENT"


class ExtractionStatus(str, Enum):
    PENDING = "PENDING"
    EXTRACTED = "EXTRACTED"
    PARTIAL = "PARTIAL"
    UNREADABLE = "UNREADABLE"
    UNSUPPORTED = "UNSUPPORTED"
    EMPTY = "EMPTY"
    ERROR = "ERROR"


class FieldResult(str, Enum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    MISSING_IN_SI = "MISSING_IN_SI"
    MISSING_IN_BL = "MISSING_IN_BL"
    LOW_CONFIDENCE_REVIEW = "LOW_CONFIDENCE_REVIEW"


class ComparisonStatus(str, Enum):
    PASSED = "PASSED"
    ATTENTION_REQUIRED = "ATTENTION_REQUIRED"
    HUMAN_REVIEW = "HUMAN_REVIEW"


class CaseStatus(str, Enum):
    RECEIVED = "RECEIVED"
    SECURITY_CHECK = "SECURITY_CHECK"
    SECURITY_REVIEW = "SECURITY_REVIEW"
    CLASSIFIED = "CLASSIFIED"
    NO_ACTION_INFO = "NO_ACTION_INFO"
    DOCUMENTS_DETECTED = "DOCUMENTS_DETECTED"
    WAITING_DOCUMENTS = "WAITING_DOCUMENTS"
    EXTRACTING = "EXTRACTING"
    COMPARING = "COMPARING"
    NO_MISMATCH_DETECTED = "NO_MISMATCH_DETECTED"
    MISMATCH_DETECTED = "MISMATCH_DETECTED"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    DRAFT_READY = "DRAFT_READY"
    NOTIFY_PARTY = "NOTIFY_PARTY"
    AWAITING_RESPONSE = "AWAITING_RESPONSE"
    ASSIGNED = "ASSIGNED"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"


class ActionType(str, Enum):
    NO_ACTION_INFORMATION = "NO_ACTION_INFORMATION"
    REVIEW_MISMATCH = "REVIEW_MISMATCH"
    REQUEST_BL_CORRECTION = "REQUEST_BL_CORRECTION"
    REQUEST_MISSING_DOCUMENT = "REQUEST_MISSING_DOCUMENT"
    SECURITY_REVIEW = "SECURITY_REVIEW"
    NOTIFY_PARTY = "NOTIFY_PARTY"
    ASSIGN_INTERNAL_USER = "ASSIGN_INTERNAL_USER"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    PREPARE_SI = "PREPARE_SI"
    ANSWER_INVOICE_QUERY = "ANSWER_INVOICE_QUERY"
    CONFIRM_DOCUMENTS = "CONFIRM_DOCUMENTS"
    PROVIDE_DRAFT_BL = "PROVIDE_DRAFT_BL"


class ReviewReason(str, Enum):
    """Why a comparison could not be decided (hackathon review_reason axis)."""
    WRONG_DOC_TYPE = "wrong_doc_type"
    MISSING_ATTACHMENT = "missing_attachment"
    UNREADABLE = "unreadable"
    MISSING_VALUE = "missing_value"


class ErrorCategory(str, Enum):
    EMAIL_CONNECTOR_ERROR = "EMAIL_CONNECTOR_ERROR"
    ATTACHMENT_DOWNLOAD_ERROR = "ATTACHMENT_DOWNLOAD_ERROR"
    UNSUPPORTED_FILE = "UNSUPPORTED_FILE"
    OCR_ERROR = "OCR_ERROR"
    EXTRACTION_ERROR = "EXTRACTION_ERROR"
    MISSING_SI = "MISSING_SI"
    MISSING_BL = "MISSING_BL"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    COMPARISON_ERROR = "COMPARISON_ERROR"
    DATABASE_ERROR = "DATABASE_ERROR"
    AUTH_ERROR = "AUTH_ERROR"
    NOTIFICATION_ERROR = "NOTIFICATION_ERROR"


class ActorType(str, Enum):
    USER = "USER"
    AI = "AI"
    SYSTEM = "SYSTEM"


class Role(str, Enum):
    OPERATIONS_STAFF = "OPERATIONS_STAFF"
    SUPERVISOR = "SUPERVISOR"
    ADMIN = "ADMIN"
    AUDITOR = "AUDITOR"


class RecipientType(str, Enum):
    INTERNAL_USER = "INTERNAL_USER"
    OPERATIONS_STAFF = "OPERATIONS_STAFF"
    SUPERVISOR = "SUPERVISOR"
    TEAM = "TEAM"
    NOTIFY_PARTY_CONTACT = "NOTIFY_PARTY_CONTACT"
    EXTERNAL_COLLABORATOR = "EXTERNAL_COLLABORATOR"


EXTERNAL_RECIPIENT_TYPES = {RecipientType.NOTIFY_PARTY_CONTACT, RecipientType.EXTERNAL_COLLABORATOR}


class DraftStatus(str, Enum):
    PROPOSED = "PROPOSED"
    EDITED = "EDITED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SENT = "SENT"
    SIMULATED = "SIMULATED"
    DELIVERING = "DELIVERING"
    DELIVERY_UNKNOWN = "DELIVERY_UNKNOWN"
    SEND_FAILED = "SEND_FAILED"


# ---------------------------------------------------------------------------
# Evidence + extraction contracts
# ---------------------------------------------------------------------------
class Evidence(BaseModel):
    document_id: Optional[str] = None
    page: Optional[int] = None
    snippet: str = ""
    label_found: Optional[str] = None  # e.g. "Load Port" (synonym resolved)
    line: Optional[int] = None


class ExtractedField(BaseModel):
    original: Optional[str] = None
    normalized: Optional[Any] = None  # str | int | float
    confidence: float = 0.0
    evidence: Evidence = Field(default_factory=Evidence)
    needs_review: bool = False
    review_note: Optional[str] = None


class SevenFieldExtraction(BaseModel):
    """Strict container for the seven fields - the AI/rule extractor output."""
    shipper: ExtractedField = Field(default_factory=ExtractedField)
    consignee: ExtractedField = Field(default_factory=ExtractedField)
    notify_party: ExtractedField = Field(default_factory=ExtractedField)
    port_of_loading: ExtractedField = Field(default_factory=ExtractedField)
    port_of_discharge: ExtractedField = Field(default_factory=ExtractedField)
    container_count: ExtractedField = Field(default_factory=ExtractedField)
    gross_weight_kg: ExtractedField = Field(default_factory=ExtractedField)

    def get(self, name: str) -> ExtractedField:
        return getattr(self, name)

    def overall_confidence(self) -> float:
        vals = [self.get(f).confidence for f in SEVEN_FIELDS]
        return round(sum(vals) / len(vals), 3) if vals else 0.0


# ---------------------------------------------------------------------------
# Comparison contracts (deterministic output)
# ---------------------------------------------------------------------------
class ComparisonField(BaseModel):
    field: str
    label: str
    si_original: Optional[str] = None
    bl_original: Optional[str] = None
    si_normalized: Optional[Any] = None
    bl_normalized: Optional[Any] = None
    result: FieldResult
    confidence: float
    reason: str
    attention: str
    si_evidence: Evidence = Field(default_factory=Evidence)
    bl_evidence: Evidence = Field(default_factory=Evidence)


class ComparisonResult(BaseModel):
    comparison_status: ComparisonStatus
    mismatch_count: int
    required_field_count: int = 7
    message: str
    fields: list[ComparisonField]
    mismatch_fields: list[str] = Field(default_factory=list)
    review_fields: list[str] = Field(default_factory=list)
    review_reason: Optional[ReviewReason] = None
    compared_at: datetime = Field(default_factory=datetime.utcnow)
    si_document_id: Optional[str] = None
    bl_document_id: Optional[str] = None
    policy_version: str = "v1"


# ---------------------------------------------------------------------------
# Classification contracts
# ---------------------------------------------------------------------------
class SecuritySignal(BaseModel):
    signal: str
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    evidence: str
    recommended_action: str


class SecurityAssessment(BaseModel):
    outcome: SecurityOutcome
    score: float  # 0 = clean, 1 = certainly spam/malicious
    signals: list[SecuritySignal] = Field(default_factory=list)
    rationale: str = ""


class IntentClassification(BaseModel):
    intent: Intent
    hackathon_category: HackathonCategory
    action_required: bool
    priority: Priority
    confidence: float
    rationale: str
    decided_by: Literal["rule", "model", "llm", "hybrid"] = "rule"
    language: str = "en"


class AttachmentClassification(BaseModel):
    attachment_id: str
    file_name: str
    file_type: str
    detected_type: DocumentType
    confidence: float
    rationale: str
    extraction_status: ExtractionStatus


# ---------------------------------------------------------------------------
# Summary / recommendation / draft
# ---------------------------------------------------------------------------
class CaseSummary(BaseModel):
    text: str
    generated_by: Literal["rule", "llm"] = "rule"
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: float = 1.0


class ActionRecommendation(BaseModel):
    action_required: bool
    action_type: ActionType
    priority: Priority
    reason: str
    recommended_action: str
    responsible_role: Role
    confidence: float


class DraftAction(BaseModel):
    id: Optional[str] = None
    draft_type: str  # CORRECTION_REQUEST | MISSING_DOCUMENT_REQUEST | CONFIRMATION | INFO_REPLY | SHARE_MESSAGE
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    subject: str
    body: str
    evidence_refs: list[str] = Field(default_factory=list)
    status: DraftStatus = DraftStatus.PROPOSED
    version: int = 1
    requires_external_approval: bool = True
    generated_by: Literal["rule", "llm"] = "rule"


class DecisionTrace(BaseModel):
    """Step-by-step record of what each pipeline node decided (for audit + UI)."""
    node: str
    actor_type: ActorType
    started_at: datetime
    finished_at: datetime
    input_ref: Optional[str] = None
    output: dict[str, Any] = Field(default_factory=dict)
    policy_version: str = "v1"


# ---------------------------------------------------------------------------
# Email / attachment / case records
# ---------------------------------------------------------------------------
class AttachmentMeta(BaseModel):
    id: str
    source_email_id: str
    file_name: str
    file_type: str
    size_bytes: int = 0
    checksum: str = ""
    storage_pointer: str = ""
    detected_type: DocumentType = DocumentType.UNKNOWN_DOCUMENT
    detection_confidence: float = 0.0
    extraction_status: ExtractionStatus = ExtractionStatus.PENDING
    extraction_confidence: float = 0.0
    raw_text: Optional[str] = None
    page_count: Optional[int] = None
    is_duplicate_of: Optional[str] = None
    reader_note: Optional[str] = None   # e.g. "Text recovered via OCR (lower confidence)."
    ocr: bool = False                   # text came from Gemini vision / pytesseract, not a text layer


class EmailMessage(BaseModel):
    id: str  # immutable source message id (email_id)
    provider: str = "bundle"
    provider_message_id: str = ""
    conversation_id: Optional[str] = None
    sender: str
    sender_name: Optional[str] = None
    recipients: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    subject: str
    body: str
    received_at: datetime
    language: str = "en"
    attachments: list[AttachmentMeta] = Field(default_factory=list)
    checksum: str = ""
    is_duplicate_of: Optional[str] = None
    mailbox_user_id: Optional[str] = None   # set when the message was pulled from a user's connected mailbox
    mailbox_address: Optional[str] = None


class ProcessingError(BaseModel):
    id: str
    case_id: str
    category: ErrorCategory
    step: str
    message: str
    safe_details: Optional[str] = None
    recovery: str
    retryable: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    resolved: bool = False


class CaseRecord(BaseModel):
    id: str
    source_email_id: str
    tenant_id: str = "tenant_april"
    intent: Intent
    hackathon_category: HackathonCategory
    action_required: bool
    priority: Priority
    status: CaseStatus
    security: SecurityAssessment
    classification: IntentClassification
    mismatch_count: int = 0
    comparison_status: Optional[ComparisonStatus] = None
    review_reason: Optional[ReviewReason] = None
    confidence: float = 0.0
    si_available: bool = False
    bl_available: bool = False
    si_document_id: Optional[str] = None
    bl_document_id: Optional[str] = None
    assigned_user_id: Optional[str] = None
    assigned_team_id: Optional[str] = None
    shared_with: list[str] = Field(default_factory=list)
    summary: Optional[CaseSummary] = None
    recommendation: Optional[ActionRecommendation] = None
    comparison: Optional[ComparisonResult] = None
    si_extraction: Optional[SevenFieldExtraction] = None
    bl_extraction: Optional[SevenFieldExtraction] = None
    drafts: list[DraftAction] = Field(default_factory=list)
    anomalies: list[SecuritySignal] = Field(default_factory=list)
    errors: list[ProcessingError] = Field(default_factory=list)
    trace: list[DecisionTrace] = Field(default_factory=list)
    processing_ms: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class AuditEvent(BaseModel):
    event_id: str
    case_id: Optional[str]
    timestamp: datetime
    actor_type: ActorType
    actor_id: str
    action: str
    before: Optional[dict[str, Any]] = None
    after: Optional[dict[str, Any]] = None
    evidence_ref: Optional[str] = None
    policy_version: str = "v1"


class ShareRecord(BaseModel):
    id: str
    case_id: str
    shared_by: str
    recipient_type: RecipientType
    recipient_user_id: Optional[str] = None
    recipient_party_id: Optional[str] = None
    recipient_label: str = ""
    is_external: bool = False
    message: str
    payload_preview: dict[str, Any] = Field(default_factory=dict)
    due_date: Optional[str] = None
    confirmation_started_at: Optional[datetime] = None
    delivery_provider: Optional[str] = None
    provider_message_id: Optional[str] = None
    delivery_accepted_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None
    viewed_at: Optional[datetime] = None
    acknowledged_at: Optional[datetime] = None
    response: Optional[str] = None
    status: str = "PENDING_CONFIRMATION"  # PENDING_CONFIRMATION | CONFIRMING | DELIVERING | DELIVERY_ACCEPTED | DELIVERY_FAILED | DELIVERY_UNKNOWN | SENT | SIMULATED | VIEWED | ACKNOWLEDGED | REJECTED


class UserRecord(BaseModel):
    id: str
    email: str
    display_name: str
    roles: list[Role]
    team_id: Optional[str] = None
    tenant_id: str = "tenant_april"
    is_external: bool = False
    auth_user_id: Optional[str] = None


class PartyContact(BaseModel):
    id: str
    name: str
    email: str
    party_name: str
    approved: bool = True
    tenant_id: str = "tenant_april"


class UserMailbox(BaseModel):
    """A user's own Gmail connected through Google sign-in. The refresh token is stored encrypted only."""
    user_id: str
    tenant_id: str = "tenant_april"
    provider: str = "gmail"
    address: str
    google_sub: Optional[str] = None
    refresh_token_enc: str
    scopes: list[str] = Field(default_factory=list)
    status: str = "active"  # active | error | revoked
    connected_at: datetime
    last_polled_at: Optional[datetime] = None
    last_error: Optional[str] = None

    def can_send(self) -> bool:
        return self.status == "active" and "https://www.googleapis.com/auth/gmail.send" in self.scopes

    def public(self) -> dict[str, Any]:
        """Safe summary for API responses (never includes the token)."""
        return {
            "connected": True, "provider": self.provider, "address": self.address, "scopes": list(self.scopes), "status": self.status,
            "can_send": self.can_send(), "connected_at": self.connected_at.isoformat(),
            "last_polled_at": self.last_polled_at.isoformat() if self.last_polled_at else None, "last_error": self.last_error,
        }


class PolicyRecord(BaseModel):
    id: str
    version: str
    name: str
    values: dict[str, Any]
    updated_by: str
    updated_at: datetime
    change_note: str = ""


# ---------------------------------------------------------------------------
# API envelopes
# ---------------------------------------------------------------------------
class ErrorEnvelope(BaseModel):
    error: str
    category: ErrorCategory
    step: Optional[str] = None
    recovery: Optional[str] = None
    retryable: bool = False


class AskRequest(BaseModel):
    question: str
    language: Optional[str] = None


class AskResponse(BaseModel):
    answer: str
    citations: list[dict[str, Any]] = Field(default_factory=list)
    grounded: bool = True
    refused: bool = False
    generated_by: Literal["rule", "llm"] = "rule"


class ShareRequest(BaseModel):
    recipient_type: RecipientType
    recipient_user_id: Optional[str] = None
    recipient_party_id: Optional[str] = None
    message: Optional[str] = None
    due_date: Optional[str] = None
    # Omitted defaults to mismatch fields; an explicitly supplied [] discloses none.
    include_fields: list[str] = Field(default_factory=list)
    confirm_external: bool = False
    preview_only: bool = False


class AssignRequest(BaseModel):
    user_id: Optional[str] = None
    team_id: Optional[str] = None
    note: Optional[str] = None


class DraftRequest(BaseModel):
    draft_type: Optional[str] = None
    language: Optional[str] = None


class DraftDecision(BaseModel):
    draft_id: str
    edited_body: Optional[str] = None
    edited_subject: Optional[str] = None
    note: Optional[str] = None


class BatchRequest(BaseModel):
    action: Literal[
        "classify", "mark_no_action", "assign", "compare", "draft",
        "export", "export_xlsx", "report_xlsx", "archive", "request_review"
    ]
    case_ids: list[str]
    params: dict[str, Any] = Field(default_factory=dict)
    confirm: bool = False


class HackathonSubmissionRow(BaseModel):
    category: HackathonCategory
    status: Literal["OK", "MISMATCH", "NEEDS_REVIEW"]
    review_reason: Optional[ReviewReason] = None
    defect_fields: list[str] = Field(default_factory=list)
    has_defect: bool = False
    decided_by: Optional[str] = None
