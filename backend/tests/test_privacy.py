"""LLM privacy: identifiers are masked before any provider call, restored afterwards, and every call is audited as metadata only."""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

from fastapi.testclient import TestClient

os.environ.setdefault("REPO_BACKEND", "memory")
os.environ["AUTO_SEED"] = "0"
os.environ["LLM_PROVIDER"] = "none"

import app.ai.llm as llm_mod  # noqa: E402
from app.ai.privacy import IdentifierMasker, case_identifier_values, privacy_mode  # noqa: E402
from app.config import get_repo  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)
ADMIN = {"X-User-Id": "u_admin_1"}

SI_TEXT = """SHIPPING INSTRUCTION
Shipper: APRIL FAR EAST (M) SDN BHD
Consignee (Non-Negotiable): EAST BRIGHT FZ-LLC
Notify: EAST BRIGHT FZ-LLC
Port of Loading (POL): NANTONG, CHINA (CNNTG)
POD: KARACHI, PAKISTAN (PKKHI)
Total Containers: 6 x 40'HC
Gross Wt (kgs): 131,058 KG
Booking Ref: ONEYSINF32871
OC No.: 5ALT-01226
Container MSKU1234567 loaded. Contact docs@vitalsolutions.sg or +971 04 4938298 for PO 26067.
"""


def test_masker_hides_every_identifier_and_restores_them_exactly():
    m = IdentifierMasker()
    masked = m.mask(SI_TEXT)
    for secret in ["APRIL FAR EAST", "EAST BRIGHT", "NANTONG", "KARACHI", "131,058", "ONEYSINF32871", "5ALT-01226", "MSKU1234567", "docs@vitalsolutions.sg", "4938298", "26067"]:
        assert secret not in masked, secret
    assert "Shipper: __ID" in masked and "SHIPPING INSTRUCTION" in masked
    assert m.leaked(masked) == []
    # the same value gets the same token (equality reasoning still works on tokens)
    consignee_token = masked.split("Consignee (Non-Negotiable): ")[1].split("\n")[0]
    assert masked.split("Notify: ")[1].split("\n")[0] == consignee_token
    assert m.unmask(masked) == SI_TEXT
    assert m.unmask({"consignee": consignee_token, "n": 1, "list": [consignee_token]}) == {"consignee": "EAST BRIGHT FZ-LLC", "n": 1, "list": ["EAST BRIGHT FZ-LLC"]}
    assert m.count >= 10


def test_case_values_are_masked_even_without_labels():
    case = SimpleNamespace(comparison=SimpleNamespace(fields=[SimpleNamespace(si_original="Moorim SP Co., Ltd", bl_original="UAB NOVAKOPA")]))
    values = case_identifier_values(case)
    m = IdentifierMasker(extra_values=values)
    out = m.mask("Why does moorim sp co., ltd differ from UAB NOVAKOPA in the draft?")
    assert "moorim" not in out.lower() and "NOVAKOPA" not in out
    assert m.unmask(out) == "Why does moorim sp co., ltd differ from UAB NOVAKOPA in the draft?", "round-trip keeps the text exactly as written"


class _Provider:
    """Stands in for the OpenAI/Gemini transport so we can inspect what would leave the desk."""

    def __init__(self, reply: str = "ok"):
        self.reply = reply
        self.seen: list[tuple[str, str]] = []

    def __call__(self, system: str, user: str, *, max_tokens: int, temperature: float):
        self.seen.append((system, user))
        return llm_mod.LLMResult(text=self.reply, provider="openai", model="test-model", latency_ms=3, usage={"input_tokens": 10, "output_tokens": 5})


def _enabled_client(monkeypatch, reply: str) -> tuple[llm_mod.LLMClient, _Provider]:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    llm_mod.reset_llm()
    c = llm_mod.LLMClient()
    c.provider, c._client = "openai", object()
    provider = _Provider(reply)
    monkeypatch.setattr(c, "_raw_complete", provider)
    return c, provider


def test_complete_masks_prompt_restores_answer_and_audits_metadata_only(monkeypatch):
    monkeypatch.setenv("LLM_PRIVACY", "mask")
    c, provider = _enabled_client(monkeypatch, "")

    def echo_consignee(system, user, **kw):
        provider.seen.append((system, user))
        token = user.split("Notify: ")[1].split("\n")[0]
        return llm_mod.LLMResult(text=f"The consignee on the SI is {token} and the BL shows {token} too.", provider="openai", model="test-model", latency_ms=3, usage={"input_tokens": 10, "output_tokens": 5})

    monkeypatch.setattr(c, "_raw_complete", echo_consignee)
    before = len([e for e in get_repo().list_audit(None) if e.action == "AI_PROVIDER_CALL"])
    res = c.complete("You are a shipping assistant for EAST BRIGHT FZ-LLC.", SI_TEXT, purpose="ask_ai", case_id="case_privacy_1")
    assert res and res.masked and res.masked_identifiers >= 10 and res.purpose == "ask_ai"
    system_sent, user_sent = provider.seen[0]
    assert "EAST BRIGHT" not in system_sent and "EAST BRIGHT" not in user_sent and "APRIL FAR EAST" not in user_sent
    assert "EAST BRIGHT FZ-LLC" in res.text, "tokens restored in the answer"
    events = [e for e in get_repo().list_audit("case_privacy_1") if e.action == "AI_PROVIDER_CALL"]
    assert len(events) == 1
    after = events[0].after
    assert after["provider"] == "openai" and after["purpose"] == "ask_ai" and after["masked"] is True and after["input_tokens"] == 10
    assert "EAST BRIGHT" not in json.dumps(after) and "prompt" not in after and "text" not in after
    assert len([e for e in get_repo().list_audit(None) if e.action == "AI_PROVIDER_CALL"]) == before + 1


def test_complete_json_unmasks_nested_values_and_extraction_stays_literal(monkeypatch):
    monkeypatch.setenv("LLM_PRIVACY", "mask")
    c, provider = _enabled_client(monkeypatch, "")

    def reply_with_tokens(system, user, **kw):
        # the model echoes tokens back; the desk must turn them into the literal document values
        token = user.split("Shipper: ")[1].split("\n")[0]
        provider.seen.append((system, user))
        return llm_mod.LLMResult(text=json.dumps({"shipper": token, "nested": {"consignee": user.split("Notify: ")[1].split("\n")[0]}}), provider="openai", model="m", latency_ms=1, usage={})

    monkeypatch.setattr(c, "_raw_complete", reply_with_tokens)
    data = c.complete_json("Extract fields.", SI_TEXT, purpose="extract")
    assert data == {"shipper": "APRIL FAR EAST (M) SDN BHD", "nested": {"consignee": "EAST BRIGHT FZ-LLC"}}
    assert "APRIL FAR EAST" not in provider.seen[0][1]


def test_privacy_off_sends_plain_text_and_policy_can_disable(monkeypatch):
    monkeypatch.setenv("LLM_PRIVACY", "off")
    assert privacy_mode() == "off"
    c, provider = _enabled_client(monkeypatch, "fine")
    res = c.complete("sys", SI_TEXT, purpose="test")
    assert res.masked is False and "APRIL FAR EAST" in provider.seen[0][1]
    monkeypatch.setenv("LLM_PRIVACY", "mask")
    assert privacy_mode() == "mask"
    saved_policies = list(get_repo().policies)
    r = client.put("/policies", json={"ai_privacy": {"mask_identifiers": False}, "change_note": "test off"}, headers=ADMIN)
    assert r.status_code == 200
    try:
        assert privacy_mode() == "off"
    finally:
        get_repo().policies = saved_policies
    assert privacy_mode() == "mask"
    llm_mod.reset_llm()


def test_health_and_rag_info_expose_privacy_posture(monkeypatch):
    monkeypatch.setenv("LLM_PRIVACY", "mask")
    h = client.get("/health").json()
    assert h["llm"]["privacy"] == "mask" and h["llm"]["provider"] == "none" and "vision_ocr" in h["llm"]
    info = client.get("/rag/info", headers=ADMIN).json()
    assert info["llm"]["privacy"] == "mask" and "calls" in info["llm"]
