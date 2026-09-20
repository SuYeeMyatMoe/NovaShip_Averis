"""
Policy evaluator - admin-configurable, versioned rules.

Deterministic. Every change to the active policy creates a new version row and
an audit event (see services/policy_service.py).
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

DEFAULT_POLICY: dict[str, Any] = {
    "verification": {
        "source_of_truth": "SI",
        "required_fields": [
            "shipper", "consignee", "notify_party", "port_of_loading",
            "port_of_discharge", "container_count", "gross_weight_kg",
        ],
        "weight_unit": "kg",
        "field_confidence_threshold": 0.85,
        "legal_name_normalization": "casefold_whitespace_only",
        "port_alias_policy": "strip_unlocode_only",
    },
    "human_review": {
        "confidence_below": 0.85,
        "missing_required_field": True,
        "external_recipient_requires_approval": True,
        "suspicious_sender_to_security_review": True,
    },
    "communication": {
        "auto_send_external": False,
        "external_drafts_require_confirmation": True,
        "internal_share_roles": ["OPERATIONS_STAFF", "SUPERVISOR", "ADMIN"],
        "external_notify_roles": ["SUPERVISOR", "ADMIN"],
    },
    "security": {
        "blocked_attachment_types": [".exe", ".bat", ".cmd", ".js", ".vbs", ".scr", ".msi", ".ps1", ".jar"],
        "suspicious_domain_words": ["prize", "claim", "verify", "parcel", "crypto", "invest"],
        "max_attachments": 10,
        "duplicate_window_hours": 72,
        "trusted_domains": ["aprilasia.com", "april.com.my"],
        "partner_domains": ["fujitogrp.com", "safqa.co.ke", "psabdp.com", "roxcel.at", "ifpla.com", "algurg.ae", "vitalsolutions.sg"],
    },
    "intent": {
        "intent_model_threshold": 0.65,
        "intent_model_rule_ceiling": 0.98,
        "intent_model_override_margin": 0.05,
        "intent_llm_threshold": 0.75,
    },
    # Operator guard: warnings only, never a lock. Learned limits come from the user's own audit history.
    "operator_guard": {
        "auto_draft_after": 3,          # N user actions on a case with no live draft -> a draft is saved, never sent
        "burst_limit": 10,              # fixed ceiling: actions per window before a warning
        "burst_window_s": 60,
        "adaptive": True,               # tighten the burst limit to baseline_multiplier x the user's usual pace
        "baseline_days": 30,            # audit history considered for the baseline
        "baseline_multiplier": 3.0,
        "min_baseline_events": 20,      # below this the fixed limit applies (not enough history to learn)
        "min_effective_burst": 3,
        "off_hours_warning": True,      # LOW warning outside the user's usual working hours (learned)
        "rapid_archive_limit": 5,       # cases archived in one batch
        "login_fail_limit": 3,
        "login_fail_window_s": 900,
        "warning_dialog": True,         # UI shows a modal warning box instead of a passing toast
    },
    # What may leave the desk towards an external model provider.
    "ai_privacy": {
        "mask_identifiers": True,       # company names, addresses, references, ports, numbers -> __IDn__ before any prompt
        "allow_vision_ocr": True,       # scanned pages are sent to Gemini vision for OCR (cannot be masked)
        "audit_provider_calls": True,   # AI_PROVIDER_CALL audit events (metadata only, never text)
        "providers_no_training_note": "OpenAI API and Gemini paid tier do not train on API traffic; the free Gemini tier may. Use a billed key in production.",
    },
}


def merged_policy(overrides: dict[str, Any] | None) -> dict[str, Any]:
    base = deepcopy(DEFAULT_POLICY)
    for section, values in (overrides or {}).items():
        if isinstance(values, dict) and isinstance(base.get(section), dict):
            base[section].update(values)
        else:
            base[section] = values
    return base


def flat_security(policy: dict[str, Any]) -> dict[str, Any]:
    sec = dict(policy.get("security", {}))
    return sec


def confidence_threshold(policy: dict[str, Any]) -> float:
    return float(policy.get("verification", {}).get("field_confidence_threshold", 0.85))


def explain_policy(policy: dict[str, Any]) -> list[str]:
    """Human-readable policy explanation used by the assistant and admin UI."""
    v, h, c, s = policy["verification"], policy["human_review"], policy["communication"], policy["security"]
    return [
        f"The Shipping Instruction ({v['source_of_truth']}) is always the source of truth.",
        f"Exactly {len(v['required_fields'])} fields are compared: " + ", ".join(v["required_fields"]) + ".",
        f"Weights are compared in {v['weight_unit']}; names are normalised by {v['legal_name_normalization'].replace('_', ' ')} (no legal-name rewriting).",
        f"Any field extracted with confidence below {h['confidence_below']:.2f} or missing is routed to HUMAN_REVIEW instead of being decided.",
        "External email is never auto-sent: " + ("every external draft requires human confirmation." if c["external_drafts_require_confirmation"] else "confirmation is optional."),
        f"Only roles {', '.join(c['external_notify_roles'])} may notify an external party; {', '.join(c['internal_share_roles'])} may share internally.",
        f"Blocked attachment types: {', '.join(s['blocked_attachment_types'])}. Max attachments per email: {s['max_attachments']}.",
        _explain_operator_guard(policy.get("operator_guard") or {}),
        _explain_ai_privacy(policy.get("ai_privacy") or {}),
    ]


def _explain_operator_guard(g: dict[str, Any]) -> str:
    base = (f"Operator guard: after {g.get('auto_draft_after', 3)} actions on a case with no live draft a draft is saved (never sent); "
            f"more than {g.get('burst_limit', 10)} actions in {g.get('burst_window_s', 60)}s raises a warning")
    if g.get("adaptive", True):
        base += (f", tightened to {g.get('baseline_multiplier', 3.0):g}x each user's usual pace once {g.get('min_baseline_events', 20)} "
                 f"audited actions exist in the last {g.get('baseline_days', 30)} days")
    base += "; warnings never lock an account."
    return base


def _explain_ai_privacy(a: dict[str, Any]) -> str:
    parts = []
    parts.append("company identifiers are masked before any prompt reaches OpenAI or Gemini" if a.get("mask_identifiers", True) else "prompts are sent to the model provider unmasked")
    parts.append("scanned pages may be sent to Gemini vision for OCR" if a.get("allow_vision_ocr", True) else "vision OCR is disabled")
    parts.append("each provider call is audited as metadata only" if a.get("audit_provider_calls", True) else "provider calls are not audited")
    return "AI privacy: " + "; ".join(parts) + ". The seven-field verdict never uses a model."
