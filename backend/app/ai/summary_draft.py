"""
Nodes 8 + 9 - Case summary and draft generation.

Both are built from the VERIFIED CaseResult only (comparison + classification).
Rule templates are always available; the LLM (if enabled) may polish wording
but is post-checked so it cannot add fields or values that are not in the
deterministic result.
"""
from __future__ import annotations

import re
from typing import Optional

from app.ai.llm import get_llm
from app.contracts.schemas import (
    FIELD_LABELS,
    NO_MISMATCH_MESSAGE,
    CaseSummary,
    ComparisonResult,
    ComparisonStatus,
    DraftAction,
    EmailMessage,
    FieldResult,
    HackathonCategory,
    Intent,
    IntentClassification,
    ReviewReason,
    SecurityAssessment,
    SecurityOutcome,
)


def _mismatch_lines(cmp: ComparisonResult) -> list[str]:
    out = []
    for f in cmp.fields:
        if f.result == FieldResult.MISMATCH:
            out.append(f"{f.label}: SI = {f.si_original} | BL = {f.bl_original}")
    return out


def build_summary(
    email: EmailMessage,
    classification: IntentClassification,
    security: SecurityAssessment,
    comparison: Optional[ComparisonResult],
    review_reason: Optional[ReviewReason],
    si_available: bool,
    bl_available: bool,
    draft_bl_requested: bool = False,
) -> CaseSummary:
    refs: list[str] = [f"email:{email.id}"]
    if security.outcome == SecurityOutcome.SPAM:
        text = "The message was classified as spam by the security precheck (" + "; ".join(s.evidence for s in security.signals[:2]) + "). No action is required."
        return CaseSummary(text=text, evidence_refs=refs)
    if security.outcome == SecurityOutcome.SECURITY_REVIEW:
        text = "The message triggered a critical security signal and is held for security review: " + "; ".join(s.evidence for s in security.signals[:2])
        return CaseSummary(text=text, evidence_refs=refs)

    intent = classification.intent
    if intent in (Intent.DOCUMENT_VERIFICATION, Intent.DOCUMENT_CORRECTION):
        head = "The customer requested Draft BL verification against the Shipping Instruction."
        if draft_bl_requested:
            return CaseSummary(text="The sender asked us to send the draft BL for checking. No SI/BL attachments yet; the case is waiting for documents and no comparison was performed.", evidence_refs=refs)
        if review_reason == ReviewReason.MISSING_ATTACHMENT or not (si_available and bl_available):
            missing = [n for n, ok in (("SI", si_available), ("Draft BL", bl_available)) if not ok]
            text = f"{head} The {' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} missing, so no comparison was performed. The case is waiting for documents."
            return CaseSummary(text=text, evidence_refs=refs)
        if review_reason == ReviewReason.WRONG_DOC_TYPE:
            text = f"{head} One attachment is not an SI or Draft BL (e.g. an invoice or packing list), so the comparison cannot be performed. A request for the correct Draft BL is ready for review."
            return CaseSummary(text=text, evidence_refs=refs)
        if review_reason == ReviewReason.UNREADABLE:
            text = f"{head} At least one attachment is unreadable (image-only scan, empty or corrupt), so extraction failed. Human review or OCR is required."
            return CaseSummary(text=text, evidence_refs=refs)
        if comparison is None:
            return CaseSummary(text=f"{head} Comparison has not completed.", evidence_refs=refs)
        refs += [f"comparison:{f.field}" for f in comparison.fields]
        matched = sum(1 for f in comparison.fields if f.result == FieldResult.MATCH)
        if comparison.comparison_status == ComparisonStatus.PASSED:
            text = f"{head} All seven required fields match. {NO_MISMATCH_MESSAGE} A confirmation draft is ready for review."
        elif comparison.mismatch_count > 0:
            details = "; ".join(_mismatch_lines(comparison))
            noun = "field differs" if comparison.mismatch_count == 1 else "fields differ"
            text = f"{head} {matched} of seven required fields match. {comparison.mismatch_count} {noun}: {details}. A correction-request draft is ready for review."
            if comparison.review_fields:
                text += " " + ", ".join(FIELD_LABELS[f] for f in comparison.review_fields) + " could not be decided and need human review."
        else:
            text = f"{head} {matched} of seven fields match; " + ", ".join(FIELD_LABELS[f] for f in comparison.review_fields) + " could not be decided (missing or low-confidence values). The case requires human review - no mismatch is asserted."
        return CaseSummary(text=text, evidence_refs=refs)
    if intent == Intent.PREPARE_SHIPPING_INSTRUCTION:
        return CaseSummary(text="The sender provided or requested a Shipping Instruction. Prepare/forward the SI and await the carrier's draft BL.", evidence_refs=refs)
    if intent == Intent.INVOICE_QUERY:
        return CaseSummary(text="The sender raised a billing / invoice question. Route to finance or reply with the requested breakdown.", evidence_refs=refs)
    return CaseSummary(text="Informational message (update, report, reminder or notice). Saved and categorised. No reply needed.", evidence_refs=refs)


# ---------------------------------------------------------------------------
# Drafts - never auto-sent; every external draft requires human approval
# ---------------------------------------------------------------------------
def _ref_from_subject(subject: str) -> str:
    m = re.search(r"\b(5[A-Z]{3}-\d{5})\b", subject)
    return m.group(1) if m else subject[:60]


def build_draft(
    email: EmailMessage,
    classification: IntentClassification,
    comparison: Optional[ComparisonResult],
    review_reason: Optional[ReviewReason],
    si_available: bool,
    bl_available: bool,
    draft_type: Optional[str] = None,
    draft_bl_requested: bool = False,
) -> Optional[DraftAction]:
    ref = _ref_from_subject(email.subject)
    sign = "\n\nBest regards,\nShipping Documentation Team\nNovaShip Averis"
    to = [email.sender]
    intent = classification.intent

    if classification.hackathon_category == HackathonCategory.SPAM:
        return None
    if intent not in (Intent.DOCUMENT_VERIFICATION, Intent.DOCUMENT_CORRECTION) and draft_type is None:
        if intent == Intent.INVOICE_QUERY:
            body = f"Dear Sender,\n\nThank you for your query regarding {ref}. We are checking the invoice details with our finance team and will revert with the breakdown shortly.{sign}"
            return DraftAction(draft_type="INFO_REPLY", to=to, subject=f"RE: {email.subject}", body=body, evidence_refs=[f"email:{email.id}"])
        if intent == Intent.PREPARE_SHIPPING_INSTRUCTION:
            body = f"Dear Sender,\n\nWe have received the Shipping Instruction for {ref}. We will forward it to the carrier and revert with the draft BL once available.{sign}"
            return DraftAction(draft_type="INFO_REPLY", to=to, subject=f"RE: {email.subject}", body=body, evidence_refs=[f"email:{email.id}"])
        return None  # informational -> No Reply Needed

    if draft_bl_requested:
        body = f"Dear Sender,\n\nNoted on your request for the draft BL for {ref}. We are obtaining the draft from the carrier and will forward it for your checking as soon as it is available.{sign}"
        return DraftAction(draft_type="INFO_REPLY", to=to, subject=f"RE: {email.subject}", body=body, evidence_refs=[f"email:{email.id}"])
    if review_reason in (ReviewReason.MISSING_ATTACHMENT, ReviewReason.WRONG_DOC_TYPE) or not (si_available and bl_available):
        missing = [n for n, ok in (("Shipping Instruction", si_available), ("Draft BL", bl_available)) if not ok]
        if review_reason == ReviewReason.WRONG_DOC_TYPE and not missing:
            missing = ["Draft Bill of Lading (the attached document is not a Draft BL)"]
        body = (f"Dear Sender,\n\nThank you for your email regarding {ref}. To complete the SI vs Draft BL verification we still need: "
                + ", ".join(missing) + ".\n\nKindly send the missing document(s) and we will verify immediately." + sign)
        return DraftAction(draft_type="MISSING_DOCUMENT_REQUEST", to=to, subject=f"RE: {email.subject} - document required", body=body, evidence_refs=[f"email:{email.id}"])
    if review_reason == ReviewReason.UNREADABLE:
        body = (f"Dear Sender,\n\nThank you for your email regarding {ref}. One of the attachments could not be read (scanned image, empty or corrupt file). "
                "Kindly resend a text-readable PDF/DOCX/XLSX copy so we can verify the Draft BL against the SI." + sign)
        return DraftAction(draft_type="MISSING_DOCUMENT_REQUEST", to=to, subject=f"RE: {email.subject} - readable copy required", body=body, evidence_refs=[f"email:{email.id}"])
    if comparison is None:
        return None
    if comparison.mismatch_count > 0:
        lines = []
        for f in comparison.fields:
            if f.result == FieldResult.MISMATCH:
                lines.append(f"  - {f.label}: Draft BL shows \"{f.bl_original}\" but the SI states \"{f.si_original}\". Please amend the BL to \"{f.si_original}\".")
        body = (f"Dear Sender,\n\nWe have checked the Draft BL against the Shipping Instruction for {ref}. "
                f"{comparison.mismatch_count} discrepanc{'y was' if comparison.mismatch_count == 1 else 'ies were'} found:\n\n" + "\n".join(lines)
                + "\n\nKindly issue a revised Draft BL reflecting the SI values. All other fields are in order." + sign)
        return DraftAction(draft_type="CORRECTION_REQUEST", to=to, subject=f"RE: {email.subject} - BL correction required",
                           body=body, evidence_refs=[f"comparison:{f}" for f in comparison.mismatch_fields])
    if comparison.review_fields:
        fields = ", ".join(FIELD_LABELS[f] for f in comparison.review_fields)
        body = (f"Dear Sender,\n\nWe have reviewed the Draft BL against the Shipping Instruction for {ref}. "
                f"The following field(s) are blank or unreadable on the SI/BL: {fields}. Kindly confirm the correct value(s) so we can complete verification." + sign)
        return DraftAction(draft_type="MISSING_VALUE_REQUEST", to=to, subject=f"RE: {email.subject} - confirmation required", body=body, evidence_refs=[f"comparison:{f}" for f in comparison.review_fields])
    body = (f"Dear Sender,\n\nWe have checked the Draft BL against the Shipping Instruction for {ref}. "
            f"All seven required fields (Shipper, Consignee, Notify Party, Port of Loading, Port of Discharge, Container Count, Gross Weight) match. {NO_MISMATCH_MESSAGE}\n\n"
            "The Draft BL is confirmed; please proceed to release." + sign)
    return DraftAction(draft_type="CONFIRMATION", to=to, subject=f"RE: {email.subject} - Draft BL confirmed", body=body, evidence_refs=[f"comparison:{f.field}" for f in comparison.fields])


_POLISH_SYSTEM = """You polish a shipping-operations email draft for tone and clarity.
Keep every value, field name, reference number and instruction EXACTLY as given. Do not add facts. Return JSON {"body": "..."}"""


def polish_with_llm(draft: DraftAction, comparison: Optional[ComparisonResult]) -> DraftAction:
    """Optional LLM polish with a guard: all SI/BL values must still be present verbatim."""
    llm = get_llm()
    if not llm.enabled:
        return draft
    data = llm.complete_json(_POLISH_SYSTEM, draft.body, max_tokens=700, purpose="draft_polish")
    if not data or not isinstance(data.get("body"), str):
        return draft
    new_body = data["body"]
    if comparison:
        for f in comparison.fields:
            if f.result == FieldResult.MISMATCH and (str(f.si_original) not in new_body or str(f.bl_original) not in new_body):
                return draft  # guard failed -> keep deterministic draft
    draft.body = new_body
    draft.generated_by = "llm"
    return draft
