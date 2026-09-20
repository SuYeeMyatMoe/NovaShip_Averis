"""
Node 4 - Seven-field extractor for SI and Draft BL documents.

Strategy (deterministic first, LLM as verified fallback):
  1. Label-synonym resolution: "Load Port" == "Port of Loading (POL)" == "POL".
     Handles  `Label: value`,  `Label (中文): value`,  and PDF layouts where the
     label sits alone on a line and the value follows on the next line.
  2. Every candidate carries evidence (document id, page, line, snippet, label).
  3. Missing / blank values are NEVER invented -> original preserved, normalized
     None, needs_review=True.
  4. Optional LLM fallback for fields the rules could not find; its answer is
     only accepted if the quoted snippet actually exists in the document.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from app.ai.llm import get_llm
from app.contracts.schemas import SEVEN_FIELDS, Evidence, ExtractedField, SevenFieldExtraction
from app.core.normalizer import is_blank, normalize_field

# Longest label first so "Notify Party" wins over "Notify", "Port of Loading (POL)" over "POL".
LABEL_SYNONYMS: dict[str, list[str]] = {
    "shipper": ["Shipper (Principal or Seller)", "Shipper/Exporter", "Shipper / Exporter", "Exporter", "Shipper", "SHIPPER"],
    "consignee": ["Consignee (Non-Negotiable)", "To the Order of", "Consignee", "CONSIGNEE"],
    "notify_party": ["Notify Party/Intermediate Consignee", "Notify Party", "Notify", "NOTIFY PARTY", "NOTIFY"],
    "port_of_loading": ["Port of Loading (POL)", "Port of Loading", "Load Port", "Loading Port", "PORT OF LOADING", "POL"],
    "port_of_discharge": ["Port of Discharge (POD)", "Port of Discharge", "Discharge Port", "Port of Discharging", "PORT OF DISCHARGE", "POD"],
    "container_count": ["No. of Containers or Packages", "No. of Containers", "Total Containers", "Container Count", "Containers", "No of Containers", "Number of Containers"],
    "gross_weight_kg": ["Gross Weight毛重(KGS)", "Gross Weight (KG)", "Gross Wt (kgs)", "Gross Weight (KGS)", "Gross Weight", "Gross Wt", "GROSS WEIGHT", "GROSS WT", "G.W."],
}

PARTY_FIELDS = {"shipper", "consignee", "notify_party"}
_PAGE_MARK = re.compile(r"^\[\[PAGE (\d+)\]\]")


@dataclass
class Candidate:
    field: str
    label: str
    raw_value: str
    line_no: int
    page: Optional[int]
    snippet: str
    inline: bool  # label + value on same line (colon form)
    total_prefix: bool = False

    def score(self) -> float:
        s = 0.0
        if self.inline:
            s += 2.0
        if not is_blank(self.raw_value):
            s += 1.0
        if self.total_prefix:
            s += 1.5
        if self.field == "gross_weight_kg" and normalize_field(self.field, self.raw_value) is not None:
            s += 1.0
        if self.field == "container_count" and normalize_field(self.field, self.raw_value) is not None:
            s += 1.0
        return s


def _label_regex(field: str) -> re.Pattern:
    labels = sorted(LABEL_SYNONYMS[field], key=len, reverse=True)
    alt = "|".join(re.escape(l) for l in labels)
    # optional "TOTAL " prefix, label, optional parenthetical groups (e.g. Chinese), optional colon, rest
    return re.compile(
        rf"^\s*(?P<total>TOTAL\s+)?(?P<label>{alt})(?P<paren>[^\x00-\x7F]*(\s*\([^)]*\))*)\s*(?P<colon>:)?\s*(?P<value>.*)$",
        re.I,
    )


_REGEX = {f: _label_regex(f) for f in SEVEN_FIELDS}


def _clean_party(value: str) -> str:
    """'NAME | ADDR; ADDR' or 'NAME; ADDR' -> 'NAME'. Never rewrites the name itself."""
    v = value.strip()
    for sep in (" | ", "|", ";"):
        if sep in v:
            v = v.split(sep)[0].strip()
    return v


def _collect_candidates(text: str, field: str) -> list[Candidate]:
    rx = _REGEX[field]
    lines = text.split("\n")
    page: Optional[int] = None
    out: list[Candidate] = []
    for i, line in enumerate(lines):
        pm = _PAGE_MARK.match(line)
        if pm:
            page = int(pm.group(1))
            continue
        m = rx.match(line)
        if not m:
            continue
        label = m.group("label")
        value = (m.group("value") or "").strip()
        has_colon = bool(m.group("colon"))
        # Short labels (POL/POD/Notify) without a colon must be alone on the line to count
        if not has_colon and value:
            # e.g. "Consignee (Non-Negotiable)" handled via paren group; anything else trailing is a false positive
            # unless the label is long (>= 3 words) - treat as inline label without colon
            if len(label.split()) < 3:
                continue
        if has_colon and value:
            out.append(Candidate(field, label, value, i, page, line.strip(), inline=True, total_prefix=bool(m.group("total"))))
        elif has_colon and not value:
            # "CONSIGNEE: " -> blank value on this line. Only treat next line as value if it is indented
            # continuation? No: in SI/BL txt a blank after colon means blank. Keep as blank candidate.
            out.append(Candidate(field, label, "", i, page, line.strip(), inline=True, total_prefix=bool(m.group("total"))))
        else:
            # label alone on the line (PDF layout) -> value is the next non-empty line
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            nxt = lines[j].strip() if j < len(lines) else ""
            # guard: the next line must not itself be another label
            if nxt and not any(_REGEX[f2].match(nxt) and not _REGEX[f2].match(nxt).group("value") for f2 in SEVEN_FIELDS if f2 != field):
                out.append(Candidate(field, label, nxt, j, page, f"{line.strip()} -> {nxt}", inline=False, total_prefix=bool(m.group("total"))))
    return out


def extract_seven_fields(text: str, document_id: str, *, use_llm: bool = True) -> SevenFieldExtraction:
    result = SevenFieldExtraction()
    missing: list[str] = []
    for field in SEVEN_FIELDS:
        cands = _collect_candidates(text, field)
        if not cands:
            missing.append(field)
            setattr(result, field, ExtractedField(
                original=None, normalized=None, confidence=0.0, needs_review=True,
                review_note=f"No label for '{field}' found in document.",
                evidence=Evidence(document_id=document_id, snippet=""),
            ))
            continue
        best = max(cands, key=lambda c: (c.score(), -c.line_no))
        raw = best.raw_value
        if field in PARTY_FIELDS:
            raw = _clean_party(raw)
        elif field == "gross_weight_kg":
            raw = raw.strip()
        normalized = normalize_field(field, raw)
        blank = is_blank(raw)
        if blank:
            conf = 0.0
        elif normalized is None:
            conf = 0.4  # label found but value not parseable -> low confidence review
        else:
            conf = 0.97 if best.inline else 0.9
            if field == "gross_weight_kg" and not best.total_prefix and not best.inline:
                conf = 0.85
        setattr(result, field, ExtractedField(
            original=raw if raw != "" else "",
            normalized=normalized,
            confidence=conf,
            needs_review=blank or normalized is None,
            review_note=("Value is blank in the document." if blank else ("Value could not be parsed safely." if normalized is None else None)),
            evidence=Evidence(document_id=document_id, page=best.page, line=best.line_no + 1, snippet=best.snippet[:200], label_found=best.label),
        ))

    if missing and use_llm and get_llm().enabled and text.strip():
        _llm_fill(result, text, document_id, missing)
    return result


_LLM_SYSTEM = """You extract shipping document fields. Return JSON:
{"fields": {"<field>": {"value": "<exact text as written or null>", "snippet": "<the exact line copied verbatim from the document>"}}}
Only use text that literally appears in the document. If a field is absent, set value to null. Never guess."""


def _llm_fill(result: SevenFieldExtraction, text: str, document_id: str, fields: list[str]) -> None:
    data = get_llm().complete_json(_LLM_SYSTEM, f"Fields: {', '.join(fields)}\n\nDocument:\n{text[:6000]}", max_tokens=600, purpose="extract")
    if not data or "fields" not in data:
        return
    for field in fields:
        item = (data["fields"] or {}).get(field) or {}
        value = item.get("value")
        snippet = (item.get("snippet") or "").strip()
        if not value or not snippet or snippet not in text:
            continue  # grounding check failed -> keep as missing
        raw = _clean_party(str(value)) if field in PARTY_FIELDS else str(value).strip()
        normalized = normalize_field(field, raw)
        setattr(result, field, ExtractedField(
            original=raw, normalized=normalized, confidence=0.8 if normalized is not None else 0.4,
            needs_review=normalized is None, review_note=None if normalized is not None else "LLM value not parseable.",
            evidence=Evidence(document_id=document_id, snippet=snippet[:200], label_found="(llm)"),
        ))
