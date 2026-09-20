"""
Desk-wide aggregates shared by the dashboard endpoints and the Excel report.

Pure functions over in-memory case/email lists so the API routes, the report
builder and tests all compute the same numbers.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from app.contracts.schemas import FIELD_LABELS, SEVEN_FIELDS, CaseStatus


def dashboard_metrics(cases, n_emails: int) -> dict[str, Any]:
    m = {
        "incoming_emails": n_emails,
        "action_required": sum(1 for c in cases if c.action_required and c.status != CaseStatus.COMPLETED),
        "no_action_required": sum(1 for c in cases if not c.action_required),
        "document_verification_cases": sum(1 for c in cases if c.hackathon_category.value == "BL_COMPARISON"),
        "mismatches_detected": sum(1 for c in cases if c.mismatch_count > 0),
        "no_mismatch_cases": sum(1 for c in cases if c.comparison and c.comparison.comparison_status.value == "PASSED"),
        "waiting_for_documents": sum(1 for c in cases if c.status == CaseStatus.WAITING_DOCUMENTS),
        "human_review": sum(1 for c in cases if c.status == CaseStatus.HUMAN_REVIEW),
        "notify_party": sum(1 for c in cases if c.status in (CaseStatus.NOTIFY_PARTY, CaseStatus.AWAITING_RESPONSE)),
        "processing_errors": sum(len([e for e in c.errors if not e.resolved]) for c in cases),
        "security_flagged": sum(1 for c in cases if c.security.outcome.value != "SAFE"),
        "completed": sum(1 for c in cases if c.status == CaseStatus.COMPLETED),
        "avg_processing_ms": round(sum(c.processing_ms for c in cases) / len(cases), 1) if cases else 0,
        "by_status": {},
        "by_intent": {},
        "by_priority": {},
    }
    for c in cases:
        m["by_status"][c.status.value] = m["by_status"].get(c.status.value, 0) + 1
        m["by_intent"][c.intent.value] = m["by_intent"].get(c.intent.value, 0) + 1
        m["by_priority"][c.priority.value] = m["by_priority"].get(c.priority.value, 0) + 1
    return m


def field_stats(cases) -> list[dict[str, Any]]:
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


def security_rows(cases, emails: dict[str, Any]) -> list[dict[str, Any]]:
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


def count_by(items, key) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        k = key(item)
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items()))


# ---------------------------------------------------------------- Excel report
REPORT_SHEETS = ["Overview", "Seven fields", "Cases", "Field results", "Security", "Drafts & delivery", "Operator activity", "Mailboxes", "Errors"]


def build_report_xlsx(repo, case_ids: Optional[list[str]] = None, *, generated_by: str = "system", now: Optional[datetime] = None) -> bytes:
    """One workbook that summarises the whole desk (or the selected cases): KPIs, seven fields, cases,
    field results, security, drafts/delivery, operator activity, mailboxes and open errors."""
    import io

    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    stamp = now or datetime.utcnow()
    wanted = set(case_ids or [])
    cases = [c for c in repo.list_cases() if not wanted or c.id in wanted]
    emails = {e.id: e for e in repo.list_emails()}
    case_ids_set = {c.id for c in cases}
    metrics = dashboard_metrics(cases, len(emails) if not wanted else len(case_ids_set))

    wb = openpyxl.Workbook()
    head_font, head_fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="C2410C")

    def sheet(title: str, headers: list[str], widths: Optional[list[int]] = None):
        ws = wb.active if wb.active.title == "Sheet" else wb.create_sheet()
        ws.title = title
        ws.append(headers)
        for i, cell in enumerate(ws[1], start=1):
            cell.font, cell.fill, cell.alignment = head_font, head_fill, Alignment(vertical="center")
            ws.column_dimensions[get_column_letter(i)].width = (widths[i - 1] if widths and i - 1 < len(widths) else max(14, min(48, len(headers[i - 1]) + 6)))
        ws.freeze_panes = "A2"
        return ws

    # 1. Overview
    ov = sheet("Overview", ["metric", "value"], [40, 24])
    ov.append(["generated_at_utc", stamp.replace(microsecond=0).isoformat()])
    ov.append(["generated_by", generated_by])
    ov.append(["scope", f"{len(cases)} selected cases" if wanted else "whole desk"])
    for key in ("incoming_emails", "document_verification_cases", "action_required", "no_action_required", "mismatches_detected", "no_mismatch_cases",
                "waiting_for_documents", "human_review", "notify_party", "completed", "security_flagged", "processing_errors", "avg_processing_ms"):
        ov.append([key, metrics[key]])
    verified = metrics["mismatches_detected"] + metrics["no_mismatch_cases"]
    ov.append(["mismatch_rate_pct", round(100 * metrics["mismatches_detected"] / verified, 1) if verified else 0])
    for group, label in (("by_status", "status"), ("by_intent", "intent"), ("by_priority", "priority")):
        ov.append([])
        ov.append([f"cases by {label}", ""])
        ov[ov.max_row][0].font = Font(bold=True)
        for k, v in sorted(metrics[group].items()):
            ov.append([k, v])
    ov.append([])
    ov.append(["cases by category", ""])
    ov[ov.max_row][0].font = Font(bold=True)
    for k, v in count_by(cases, lambda c: c.hackathon_category.value).items():
        ov.append([k, v])
    ov.append([])
    ov.append(["cases by security outcome", ""])
    ov[ov.max_row][0].font = Font(bold=True)
    for k, v in count_by(cases, lambda c: c.security.outcome.value).items():
        ov.append([k, v])

    # 2. Seven fields
    sf = sheet("Seven fields", ["field", "label", "match", "mismatch", "missing_or_review", "example_case", "example_si", "example_bl"], [20, 22, 10, 10, 18, 22, 32, 32])
    for st in field_stats(cases):
        ex = st["examples"][0] if st["examples"] else {}
        sf.append([st["field"], st["label"], st["match"], st["mismatch"], st["review"], ex.get("case_id", ""), ex.get("si", ""), ex.get("bl", "")])

    # 3. Cases + 4. Field results
    cs = sheet("Cases", ["case_id", "email_id", "received_at", "mailbox", "sender", "subject", "intent", "category", "security", "priority", "status", "comparison_status",
                         "mismatch_count", "mismatch_fields", "review_reason", "confidence", "assigned_user_id", "drafts", "open_errors", "processing_ms", "updated_at"],
               [22, 18, 20, 26, 28, 44, 24, 18, 16, 10, 22, 18, 10, 30, 18, 10, 16, 8, 10, 12, 20])
    fr = sheet("Field results", ["case_id", "field", "result", "si_original", "bl_original", "confidence", "reason"], [22, 20, 22, 36, 36, 10, 48])
    for c in cases:
        e = emails.get(c.source_email_id)
        cs.append([c.id, c.source_email_id, e.received_at.isoformat() if e else "", (e.mailbox_address if e and e.mailbox_address else "shared") if e else "",
                   e.sender if e else "", e.subject if e else "", c.intent.value, c.hackathon_category.value, c.security.outcome.value, c.priority.value, c.status.value,
                   c.comparison_status.value if c.comparison_status else "", c.mismatch_count, "|".join(c.comparison.mismatch_fields) if c.comparison else "",
                   c.review_reason.value if c.review_reason else "", c.confidence, c.assigned_user_id or "", len(c.drafts), len([x for x in c.errors if not x.resolved]),
                   c.processing_ms, c.updated_at.isoformat()])
        if c.comparison:
            for fld in c.comparison.fields:
                fr.append([c.id, fld.field, fld.result.value, fld.si_original or "", fld.bl_original or "", fld.confidence, fld.reason])

    # 5. Security
    se = sheet("Security", ["case_id", "outcome", "score", "status", "sender", "subject", "signal", "severity", "evidence", "recommended_action"], [22, 16, 8, 20, 28, 40, 30, 10, 60, 40])
    for row in security_rows(cases, emails):
        entries = row["signals"] + row["anomalies"]
        if not entries:
            se.append([row["case_id"], row["outcome"], row["score"], row["status"], row["sender"], row["subject"], "", "", "", ""])
        for sig in entries:
            se.append([row["case_id"], row["outcome"], row["score"], row["status"], row["sender"], row["subject"], sig.get("signal"), sig.get("severity"), sig.get("evidence"), sig.get("recommended_action")])

    # 6. Drafts & delivery
    dd = sheet("Drafts & delivery", ["group", "key", "count"], [24, 36, 10])
    drafts = [d for c in cases for d in c.drafts]
    for k, v in count_by(drafts, lambda d: d.draft_type).items():
        dd.append(["draft_type", k, v])
    for k, v in count_by(drafts, lambda d: d.status.value).items():
        dd.append(["draft_status", k, v])
    dd.append(["external_approval_required", "yes", sum(1 for d in drafts if d.requires_external_approval)])
    shares = [s for s in repo.list_shares() if s.case_id in case_ids_set]
    for k, v in count_by(shares, lambda s: ("external" if s.is_external else "internal") + ":" + s.status).items():
        dd.append(["share_status", k, v])

    # 7. Operator activity
    oa = sheet("Operator activity", ["actor_type", "actor_id", "action", "count", "last_seen"], [12, 22, 36, 8, 20])
    audit = [a for a in repo.list_audit(None) if not wanted or a.case_id in case_ids_set or a.case_id is None]
    grouped: dict[tuple[str, str, str], list] = {}
    for a in audit:
        grouped.setdefault((a.actor_type.value, a.actor_id, a.action), []).append(a.timestamp)
    for (atype, actor, action), stamps in sorted(grouped.items()):
        oa.append([atype, actor, action, len(stamps), max(stamps).isoformat()])
    oa.append([])
    oa.append(["warnings", "", "", "", ""])
    oa[oa.max_row][0].font = Font(bold=True)
    oa.append(["timestamp", "actor", "signal", "case_id", "evidence"])
    for cell in oa[oa.max_row]:
        cell.font = Font(bold=True)
    for a in audit:
        if a.action in {"UNUSUAL_OPERATOR_BEHAVIOUR", "AUTO_DRAFT_AFTER_REPEATED_ACTIONS"}:
            after = a.after or {}
            oa.append([a.timestamp.isoformat(), after.get("actor") or a.actor_id, after.get("signal") or a.action, a.case_id or "", after.get("evidence") or f"mutations={after.get('mutation_count', '')}"])

    # 8. Mailboxes
    mb = sheet("Mailboxes", ["mailbox", "owner_user_id", "status", "can_send", "cases", "mismatch_cases", "human_review", "last_polled_at", "last_error"], [30, 18, 10, 10, 8, 14, 12, 20, 24])
    by_box: dict[str, list] = {}
    for c in cases:
        e = emails.get(c.source_email_id)
        by_box.setdefault((e.mailbox_address if e and e.mailbox_address else "shared"), []).append(c)
    boxes = {m.address: m for m in repo.list_mailboxes()}
    for address in sorted(set(by_box) | set(boxes)):
        m = boxes.get(address)
        rows = by_box.get(address, [])
        mb.append([address, m.user_id if m else "", m.status if m else ("shared" if address == "shared" else "disconnected"), (m.can_send() if m else address == "shared"),
                   len(rows), sum(1 for c in rows if c.mismatch_count), sum(1 for c in rows if c.status == CaseStatus.HUMAN_REVIEW),
                   m.last_polled_at.isoformat() if m and m.last_polled_at else "", (m.last_error or "") if m else ""])

    # 9. Errors
    er = sheet("Errors", ["case_id", "category", "step", "message", "recovery", "retryable", "resolved"], [22, 22, 20, 48, 48, 10, 10])
    for c in cases:
        for x in c.errors:
            er.append([c.id, x.category.value, x.step, x.message, x.recovery, x.retryable, x.resolved])

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
