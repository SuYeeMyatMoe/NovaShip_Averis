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

                self.model = self.model or os.environ.get("GEMINI_CHAT_MODEL", "gemini-2.0-flash")
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

    def complete(self, system: str, user: str, *, max_tokens: int = 800, temperature: float = 0.0) -> Optional[LLMResult]:
        if not self.enabled:
            return None
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
        res = self.complete(system + "\nRespond with a single JSON object and nothing else.", user, **kw)
        if not res or res.text.startswith("__LLM_ERROR__"):
            return None
        return parse_json_block(res.text)


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
