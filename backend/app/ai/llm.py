"""
LLM provider abstraction.

    LLM_PROVIDER = openai | gemini | none      (default: none)
    OPENAI_API_KEY / GOOGLE_API_KEY
    LLM_MODEL (optional override)

With `none` every AI node falls back to deterministic rules so the whole
platform runs offline (demo-safe). With a key configured, the LLM is used for:
  * intent classification on ambiguous emails (rule confidence < threshold)
  * extraction fallback when label-based parsing misses a field
  * case summaries / drafts / Ask-AI answers / translation
The seven-field MATCH/MISMATCH decision is NEVER delegated to the LLM.
Prompts are not persisted; only token usage metadata may be returned.

Privacy (app.ai.privacy): unless LLM_PRIVACY=off or policy `ai_privacy.mask_identifiers`
is false, company names, references, addresses and document values are replaced by
__IDn__ tokens before the provider call and restored in the answer. OpenAI requests are
sent with store=false. Every provider call is audited as AI_PROVIDER_CALL with
metadata only (provider, model, purpose, tokens, masked identifier count) - never text.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    latency_ms: int
    usage: dict[str, Any]
    purpose: str = "general"
    masked: bool = False
    masked_identifiers: int = 0


_calls: dict[str, int] = {"total": 0, "masked": 0, "errors": 0}


def llm_call_stats() -> dict[str, int]:
    return dict(_calls)


class LLMClient:
    def __init__(self) -> None:
        self.provider = os.environ.get("LLM_PROVIDER", "none").lower().strip()
        self.model = os.environ.get("LLM_MODEL", "")
        self._client = None
        self._gemini = None
        if self.provider == "openai" and os.environ.get("OPENAI_API_KEY"):
            try:
                from openai import OpenAI

                self._client = OpenAI()
                self.model = self.model or "gpt-4o-mini"
            except Exception:
                self.provider = "none"
        elif self.provider == "gemini" and os.environ.get("GOOGLE_API_KEY"):
            try:
                from langchain_google_genai import ChatGoogleGenerativeAI

                self.model = self.model or os.environ.get("GEMINI_CHAT_MODEL", "gemini-3.6-flash")
                self._gemini = ChatGoogleGenerativeAI(
                    model=self.model,
                    google_api_key=os.environ["GOOGLE_API_KEY"],
                    temperature=0,
                )
            except Exception:
                self.provider = "none"
        else:
            self.provider = "none"

    @property
    def enabled(self) -> bool:
        return self.provider != "none" and (self._client is not None or self._gemini is not None)

    def complete(self, system: str, user: str, *, max_tokens: int = 800, temperature: float = 0.0,
                 purpose: str = "general", mask: Optional[bool] = None, case_id: Optional[str] = None,
                 extra_values: Any = (), masker: Any = None) -> Optional[LLMResult]:
        """One provider call. `mask=None` follows privacy_mode(); pass `masker` to reuse a caller's mapping
        (e.g. translation checks token preservation itself and unmasks later)."""
        if not self.enabled:
            return None
        from app.ai.privacy import IdentifierMasker, privacy_mode

        do_mask = privacy_mode() == "mask" if mask is None else bool(mask)
        active = masker
        if do_mask and active is None:
            active = IdentifierMasker(extra_values=extra_values)
        sent_system, sent_user = (active.mask(system), active.mask(user)) if (do_mask and active is not None) else (system, user)
        result = self._raw_complete(sent_system, sent_user, max_tokens=max_tokens, temperature=temperature)
        if result is None:
            return None
        result.purpose = purpose
        result.masked = bool(do_mask and active is not None)
        result.masked_identifiers = active.count if active is not None else 0
        if result.masked and masker is None and not result.text.startswith("__LLM_ERROR__"):
            result.text = active.unmask(result.text)
        self._audit(result, case_id)
        return result

    def _audit(self, result: "LLMResult", case_id: Optional[str]) -> None:
        """AI_PROVIDER_CALL: metadata only. Never the prompt, never the completion."""
        _calls["total"] += 1
        if result.masked:
            _calls["masked"] += 1
        if result.text.startswith("__LLM_ERROR__"):
            _calls["errors"] += 1
        try:
            from app.ai.privacy import privacy_settings

            if not privacy_settings()["audit_provider_calls"]:
                return
            from datetime import datetime
            from uuid import uuid4

            from app.config import get_repo
            from app.contracts.schemas import ActorType, AuditEvent

            get_repo().append_audit(AuditEvent(
                event_id=f"evt_llm_{uuid4().hex}", case_id=case_id, timestamp=datetime.utcnow(), actor_type=ActorType.SYSTEM, actor_id="llm",
                action="AI_PROVIDER_CALL",
                after={"provider": result.provider, "model": result.model, "purpose": result.purpose, "masked": result.masked,
                       "masked_identifiers": result.masked_identifiers, "input_tokens": result.usage.get("input_tokens"),
                       "output_tokens": result.usage.get("output_tokens"), "latency_ms": result.latency_ms,
                       "error": result.text.split(" ", 1)[1] if result.text.startswith("__LLM_ERROR__") else None},
            ))
        except Exception:  # auditing must never break the call
            return

    def _raw_complete(self, system: str, user: str, *, max_tokens: int, temperature: float) -> Optional[LLMResult]:
        t0 = time.time()
        try:
            if self.provider == "gemini":
                from langchain_core.messages import HumanMessage, SystemMessage

                resp = self._gemini.invoke([SystemMessage(content=system), HumanMessage(content=user)])
                text = getattr(resp, "content", None) or str(resp)
                usage: dict[str, Any] = {}
                meta = getattr(resp, "usage_metadata", None) or {}
                if meta:
                    usage = {"input_tokens": meta.get("input_tokens"), "output_tokens": meta.get("output_tokens")}
            else:
                resp = self._client.chat.completions.create(
                    model=self.model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    store=False,  # do not retain the request in the provider dashboard
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                )
                text = resp.choices[0].message.content or ""
                usage = {"input_tokens": resp.usage.prompt_tokens, "output_tokens": resp.usage.completion_tokens}
        except Exception as exc:  # network / auth failure -> graceful fallback
            return LLMResult(text=f"__LLM_ERROR__ {type(exc).__name__}", provider=self.provider, model=self.model,
                             latency_ms=int((time.time() - t0) * 1000), usage={})
        return LLMResult(text=text, provider=self.provider, model=self.model,
                         latency_ms=int((time.time() - t0) * 1000), usage=usage)

    def complete_json(self, system: str, user: str, **kw) -> Optional[dict[str, Any]]:
        """JSON answer with identifiers restored inside the parsed values (tokens never leak into stored fields)."""
        from app.ai.privacy import IdentifierMasker, privacy_mode

        mask = kw.pop("mask", None)
        do_mask = privacy_mode() == "mask" if mask is None else bool(mask)
        masker = kw.pop("masker", None) or (IdentifierMasker(extra_values=kw.pop("extra_values", ())) if do_mask else None)
        kw.pop("extra_values", None)
        res = self.complete(system + "\nRespond with a single JSON object and nothing else. Keep every __IDn__ token exactly as given.", user,
                            mask=do_mask, masker=masker, **kw)
        if not res or res.text.startswith("__LLM_ERROR__"):
            return None
        parsed = parse_json_block(res.text)
        return masker.unmask(parsed) if (parsed is not None and masker is not None) else parsed


def parse_json_block(text: str) -> Optional[dict[str, Any]]:
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


_client: Optional[LLMClient] = None


def get_llm() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client


def reset_llm() -> None:
    global _client
    _client = None
