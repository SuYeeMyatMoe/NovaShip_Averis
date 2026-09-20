"""
Identifier masking for everything that leaves the desk towards a model provider.

    masker = IdentifierMasker(extra_values=[...seven-field values of the case...])
    prompt  = masker.mask(text)          # "EAST BRIGHT FZ-LLC" -> "__ID3__" (same value, same token)
    answer  = masker.unmask(llm_output)  # tokens restored in text, dicts and lists

What is masked: labelled document values (Shipper:, Consignee:, POL:, ...), company names
with a legal suffix, e-mail addresses, phone numbers, container numbers, booking / BL / OC /
PO / SO references, weights and container counts, plus any extra literal values supplied by
the caller. Masking is deterministic inside one masker, so equality reasoning ("SI value ==
BL value?") still works on the tokens, while the provider never sees the real names.

`privacy_mode()` decides whether masking is on: env LLM_PRIVACY (mask | off, default mask)
and the `ai_privacy.mask_identifiers` policy flag when a repository is available.
"""
from __future__ import annotations

import os
import re
from typing import Any, Iterable, Optional

_TOKEN = re.compile(r"__ID\d+__")

_LABELED_VALUE = re.compile(
    r"(?im)^((?:Shipper(?:/Exporter)?|Consignee(?: \(Non-Negotiable\))?|To the Order of|Notify Party|Notify|"
    r"Port of Loading(?: \(POL\))?|Load Port|POL|Port of Discharge|Discharge Port|POD|"
    r"No\. of Containers or Packages|Total Containers|Container Count|Gross Weight(?: \(KG\))?|"
    r"Gross Wt(?: \(kgs\))?|Booking Ref|Bill of Lading No\.|OC No\.|Vessel|Export Carrier(?: \(vessel, voyage\))?)\s*:\s*)(.+)$"
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_COMPANY = re.compile(
    r"(?<!\w)(?:[A-Z0-9][A-Z0-9&.,'()/-]*\s+){1,10}"
    r"(?:LTD|LIMITED|LLC|LLP|INC|CORP|CORPORATION|SDN\s+BHD|PTE\s+LTD|"
    r"PTY\s+LTD|CO\.,?\s+LTD|FZE|FZ-LLC|GMBH|PLC|S\.?A\.?|S\.?P\.?A\.?|B\.?V\.?)\b",
    re.I,
)
_CONTAINER = re.compile(r"\b[A-Z]{4}\d{7}\b")
_REFERENCE = re.compile(r"\b(?:PO|OC|SO|DO|BL|B/L|HBL|MBL|REF|BOOKING)\s?(?:NO\.?|#)?\s?-?\s?[A-Z0-9-]*\d[A-Z0-9-]*\b", re.I)
_CODE = re.compile(r"\b[A-Z]{3,}[A-Z0-9\-]*\d[A-Z0-9\-]*\b|\b\d[\d,\.]*\s?(?:KG|kg|MT|x\s?\d0'[A-Z]{2})\b|\b5[A-Z]{3}-\d{5}\b")
_PHONE = re.compile(r"(?<![\w/])\+?\d[\d\s().-]{7,}\d(?![\w/])")


def privacy_mode() -> str:
    """'mask' unless LLM_PRIVACY=off or the active policy turns identifier masking off."""
    env_mode = os.environ.get("LLM_PRIVACY", "mask").strip().lower()
    if env_mode == "off":
        return "off"
    try:
        from app.config import get_repo
        from app.core.policy import merged_policy

        flag = merged_policy(get_repo().get_active_policy().values).get("ai_privacy", {}).get("mask_identifiers", True)
        return "mask" if flag else "off"
    except Exception:
        return "mask"


def privacy_settings() -> dict[str, Any]:
    try:
        from app.config import get_repo
        from app.core.policy import merged_policy

        section = merged_policy(get_repo().get_active_policy().values).get("ai_privacy", {})
    except Exception:
        section = {}
    return {
        "mask_identifiers": privacy_mode() == "mask",
        "allow_vision_ocr": bool(section.get("allow_vision_ocr", True)),
        "audit_provider_calls": bool(section.get("audit_provider_calls", True)),
    }


class IdentifierMasker:
    def __init__(self, extra_values: Iterable[Any] = ()) -> None:
        self._forward: dict[str, str] = {}   # normalised value -> token
        self._backward: dict[str, str] = {}  # token -> original value (first spelling seen)
        self._extra = sorted({str(v).strip() for v in extra_values if v is not None and str(v).strip() and len(str(v).strip()) >= 3}, key=len, reverse=True)

    # ---- helpers
    @staticmethod
    def _norm(value: str) -> str:
        return re.sub(r"\s+", " ", value.strip()).casefold()

    def _token(self, value: str) -> str:
        value = value.strip()
        if not value or _TOKEN.fullmatch(value):
            return value
        key = self._norm(value)
        token = self._forward.get(key)
        if token is None:
            token = f"__ID{len(self._forward) + 1}__"
            self._forward[key] = token
            self._backward[token] = value
        return token

    @property
    def count(self) -> int:
        return len(self._forward)

    @property
    def mapping(self) -> dict[str, str]:
        return dict(self._backward)

    # ---- masking
    def mask(self, text: str) -> str:
        if not text:
            return text
        out = text
        for value in self._extra:
            out = re.sub(re.escape(value), lambda m: self._token(m.group(0)), out, flags=re.I)
        out = _LABELED_VALUE.sub(lambda m: m.group(1) + self._token(m.group(2)), out)
        out = _EMAIL.sub(lambda m: self._token(m.group(0)), out)
        out = _COMPANY.sub(lambda m: self._token(m.group(0)), out)
        out = _CONTAINER.sub(lambda m: self._token(m.group(0)), out)
        out = _REFERENCE.sub(lambda m: self._token(m.group(0)), out)
        out = _CODE.sub(lambda m: self._token(m.group(0)), out)
        out = _PHONE.sub(lambda m: self._token(m.group(0)), out)
        return out

    def unmask(self, value: Any) -> Any:
        if isinstance(value, str):
            return _TOKEN.sub(lambda m: self._backward.get(m.group(0), m.group(0)), value) if self._backward else value
        if isinstance(value, dict):
            return {k: self.unmask(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.unmask(v) for v in value]
        return value

    def tokens_in(self, text: str) -> list[str]:
        return _TOKEN.findall(text or "")

    def leaked(self, text: str) -> list[str]:
        """Original values that still appear verbatim in `text` (should be empty after mask())."""
        low = (text or "").casefold()
        return [orig for orig in self._backward.values() if self._norm(orig) in low]


def case_identifier_values(case: Optional[Any]) -> list[str]:
    """Seven-field originals of a case so they are masked even where no label precedes them."""
    values: list[str] = []
    comparison = getattr(case, "comparison", None) if case is not None else None
    for fld in (getattr(comparison, "fields", None) or []):
        for attr in ("si_original", "bl_original"):
            v = getattr(fld, attr, None)
            if v:
                values.append(str(v))
    return values
