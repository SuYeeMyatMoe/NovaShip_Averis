# Frozen Contracts (Day 19, 11:00 freeze)

Source of truth: [`backend/app/contracts/schemas.py`](../backend/app/contracts/schemas.py).
Frontend mirror: [`frontend/lib/api.ts`](../frontend/lib/api.ts). **Add fields; never rename or remove.**

## The seven fields (fixed baseline)
`shipper · consignee · notify_party · port_of_loading · port_of_discharge · container_count · gross_weight_kg`

All-match message is the exact string **`No mismatch detected.`** (`NO_MISMATCH_MESSAGE`).

## Enums
| Enum | Literals |
|---|---|
| `SecurityOutcome` | SAFE · SPAM · SUSPICIOUS · SECURITY_REVIEW |
| `Intent` | DOCUMENT_VERIFICATION · PREPARE_SHIPPING_INSTRUCTION · INVOICE_QUERY · DOCUMENT_CORRECTION · OPERATIONAL_UPDATE · GENERAL_ENQUIRY · INFORMATION_ONLY · NO_ACTION_REQUIRED · UNKNOWN_REVIEW |
| `HackathonCategory` | BL_COMPARISON · SI_REQUEST · INVOICE_QUERY · GENERAL · SPAM |
| `Priority` | LOW · MEDIUM · HIGH · CRITICAL |
| `DocumentType` | SHIPPING_INSTRUCTION · DRAFT_BL · INVOICE · SUPPORTING_DOCUMENT · UNKNOWN_DOCUMENT |
| `ExtractionStatus` | PENDING · EXTRACTED · PARTIAL · UNREADABLE · UNSUPPORTED · EMPTY · ERROR |
| `FieldResult` | MATCH · MISMATCH · MISSING_IN_SI · MISSING_IN_BL · LOW_CONFIDENCE_REVIEW |
| `ComparisonStatus` | PASSED · ATTENTION_REQUIRED · HUMAN_REVIEW |
| `CaseStatus` | RECEIVED · SECURITY_CHECK · SECURITY_REVIEW · CLASSIFIED · NO_ACTION_INFO · DOCUMENTS_DETECTED · WAITING_DOCUMENTS · EXTRACTING · COMPARING · NO_MISMATCH_DETECTED · MISMATCH_DETECTED · HUMAN_REVIEW · DRAFT_READY · NOTIFY_PARTY · AWAITING_RESPONSE · ASSIGNED · COMPLETED · ERROR |
| `ActionType` | NO_ACTION_INFORMATION · REVIEW_MISMATCH · REQUEST_BL_CORRECTION · REQUEST_MISSING_DOCUMENT · SECURITY_REVIEW · NOTIFY_PARTY · ASSIGN_INTERNAL_USER · HUMAN_REVIEW · PREPARE_SI · ANSWER_INVOICE_QUERY · CONFIRM_DOCUMENTS · PROVIDE_DRAFT_BL |
| `ReviewReason` | wrong_doc_type · missing_attachment · unreadable · missing_value |
| `ErrorCategory` | EMAIL_CONNECTOR_ERROR · ATTACHMENT_DOWNLOAD_ERROR · UNSUPPORTED_FILE · OCR_ERROR · EXTRACTION_ERROR · MISSING_SI · MISSING_BL · LOW_CONFIDENCE · COMPARISON_ERROR · DATABASE_ERROR · AUTH_ERROR · NOTIFICATION_ERROR |
| `Role` | OPERATIONS_STAFF · SUPERVISOR · ADMIN · AUDITOR |
| `RecipientType` | INTERNAL_USER · OPERATIONS_STAFF · SUPERVISOR · TEAM · NOTIFY_PARTY_CONTACT · EXTERNAL_COLLABORATOR |
| `DraftStatus` | PROPOSED · EDITED · APPROVED · REJECTED · SENT |
| `ActorType` | USER · AI · SYSTEM |

## Core shapes
```jsonc
// ExtractedField (per field, per document)
{ "original": "3 x 40'HC", "normalized": 3, "confidence": 0.97, "needs_review": false,
  "evidence": { "document_id": "att_email_004_SI_ab12", "page": 1, "line": 11, "snippet": "Total Containers: 3 x 40'HC", "label_found": "Total Containers" } }

// ComparisonField (deterministic)
{ "field": "container_count", "label": "Container Count", "si_original": "3 x 40'HC", "bl_original": "4 x 40'HC",
  "si_normalized": 3, "bl_normalized": 4, "result": "MISMATCH", "confidence": 0.97,
  "reason": "Container Count differs: SI = '3 x 40'HC', BL = '4 x 40'HC'.", "attention": "Verify and correct the Draft BL container count.",
  "si_evidence": {...}, "bl_evidence": {...} }

// ComparisonResult (case level)
{ "comparison_status": "ATTENTION_REQUIRED", "mismatch_count": 1, "required_field_count": 7,
  "message": "1 mismatch detected: Container Count.", "fields": [7 x ComparisonField],
  "mismatch_fields": ["container_count"], "review_fields": [], "review_reason": null, "compared_at": "...", "policy_version": "v1" }

// CaseSummary          { "text": "...", "generated_by": "rule|llm", "evidence_refs": ["email:email_004","comparison:consignee"], "confidence": 1.0 }
// ActionRecommendation { "action_required": true, "action_type": "REQUEST_BL_CORRECTION", "priority": "HIGH", "reason": "...", "recommended_action": "...", "responsible_role": "OPERATIONS_STAFF", "confidence": 0.97 }
// DraftAction          { "id": "draft_…", "draft_type": "CORRECTION_REQUEST|MISSING_DOCUMENT_REQUEST|MISSING_VALUE_REQUEST|CONFIRMATION|INFO_REPLY|SHARE_MESSAGE", "to": [...], "subject": "...", "body": "...", "status": "PROPOSED", "version": 1, "requires_external_approval": true, "evidence_refs": [...] }
// DecisionTrace        { "node": "seven_field_comparator", "actor_type": "SYSTEM", "started_at": "...", "finished_at": "...", "output": {...}, "policy_version": "v1" }
// AuditEvent           { "event_id", "case_id", "timestamp", "actor_type", "actor_id", "action", "before", "after", "evidence_ref", "policy_version" }
// ErrorEnvelope (HTTP 4xx/5xx detail) { "error": "...", "category": "MISSING_BL", "step": "...", "recovery": "...", "retryable": true }
```

## Hackathon submission row (external contract)
```json
{ "category": "BL_COMPARISON", "status": "MISMATCH", "review_reason": null, "defect_fields": ["container_count"], "has_defect": true, "decided_by": "rule" }
```
Mapping: `Intent → HackathonCategory` via `INTENT_TO_CATEGORY`; `status` = NEEDS_REVIEW iff `case.review_reason` set, MISMATCH iff `mismatch_count>0`, else OK.

## API (all mutations audited)
| Method | Path | Purpose | Permission |
|---|---|---|---|
| POST | `/webhooks/email` | ingest one message (+base64 attachments), idempotent | ingest |
| POST | `/connectors/poll` | pull from Gmail API or bundle | ingest |
| POST | `/ingest/bundle?limit=` | import local fixtures | ingest |
| GET | `/dashboard/metrics` | top metrics | view_case |
| GET | `/cases?status=&priority=&intent=&mismatch=yes|no&assigned=&shared=&sender=&q=&min_confidence=&security=&date_from=&date_to=&sort=` | list/filter | view_case |
| GET | `/cases/{id}` · `/comparison` · `/report` · `/audit` · `/documents/{att}` · `/documents/{att}/raw` | read | view_case / view_audit / view_document |
| POST | `/cases/{id}/classify` · `/extract` · `/compare` · `/retry` · `/upload` | pipeline steps | compare |
| POST | `/cases/{id}/draft` · `/draft/edit` · `/approve` · `/reject` | human-in-the-loop | generate_draft / approve_send |
| POST | `/cases/{id}/assign` · `/no-action` · `/complete` · `/request-review` | case actions | assign / view_case |
| POST | `/cases/{id}/notify-party` · GET `/recipients` · POST `/share` · `/share/{sid}/confirm` · `/shares/{sid}/acknowledge` | Notify Party / sharing | share_internal / notify_external |
| POST | `/cases/{id}/ask` · `/translate` | grounded assistant | view_case |
| POST | `/cases/batch` | classify/mark_no_action/assign/compare/draft/export/archive/request_review | batch |
| GET | `/export/cases.csv` · `/export/submission.json` | exports | export_data |
| GET/PUT | `/policies` | versioned policy | view_case / edit_policy |
| GET | `/agent/graph` · `/agent/state/{id}` | LangGraph mermaid / per-case state (paused, next, interrupt payload, trace) | view_case |
| POST | `/agent/run/{id}` · `/agent/resume/{id}` | run the graph / resume with `{action, draft_id?, edited_body?, note?, recipient?}` | compare / generate_draft |
| GET/POST | `/rag/info` · `/rag/search` · `/rag/reindex` | retrieval over knowledge + this case only | view_case / edit_policy |
| GET | `/dashboard/fields` · `/dashboard/field/{field}?result=` | seven-field statistics and per-field case list | view_case |
| GET | `/security/queue` · `/audit?limit=&action=&actor_type=` | security agent queue / global audit | view_case / view_audit |

Auth: demo mode `X-User-Id: u_ops_1|u_sup_1|u_admin_1|u_audit_1`; prod `Authorization: Bearer <supabase jwt>`.
