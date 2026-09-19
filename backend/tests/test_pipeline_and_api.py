"""Pipeline, extraction, security, Notify Party, RBAC and E2E API tests (spec section 27)."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("REPO_BACKEND", "memory")
os.environ["AUTO_SEED"] = "0"
os.environ["LLM_PROVIDER"] = "none"

from app.ai.extractor import extract_seven_fields  # noqa: E402
from app.ai.security_precheck import assess_security  # noqa: E402
from app.contracts.schemas import (  # noqa: E402
    CaseStatus,
    EmailMessage,
    RecipientType,
    SecurityOutcome,
    ShareRequest,
)
from app.main import app  # noqa: E402
from app.readers.document_reader import read_document  # noqa: E402
from app.repositories.memory import MemoryRepository  # noqa: E402
from app.repositories.supabase_repo import SupabaseRepository  # noqa: E402
from app.services.case_service import CaseService  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "sdoc-hackathon-bundle"
client = TestClient(app)

SUP = {"X-User-Id": "u_sup_1"}     # supervisor
OPS = {"X-User-Id": "u_ops_1"}     # operations staff
AUD = {"X-User-Id": "u_audit_1"}   # auditor

SI_TXT = """SHIPPING INSTRUCTION
========================================

Shipper/Exporter: APRIL FAR EAST (M) SDN BHD
  TOWER 2, AVENUE 5, LEVEL 6; KUALA LUMPUR, MALAYSIA
CONSIGNEE: MOORIM SP CO., LTD
  656, GANGNAM-DAERO; SEOUL, SOUTH KOREA
NOTIFY PARTY: UAB NOVAKOPA
Port of Loading: PORT KLANG (WESTPORT), MALAYSIA (MYPKG)
Discharge Port: CALLAO, PERU (PECLL)
No. of Containers or Packages: 3 x 40'HC
Gross Weight (KG): 22,000 KG
Booking Ref: MSDUL0942518196
"""
BL_TXT = """BILL OF LADING (DRAFT)
========================================

SHIPPER: APRIL FAR EAST (M) SDN BHD
  TOWER 2, AVENUE 5, LEVEL 6; KUALA LUMPUR, MALAYSIA
To the Order of: MOORIM SP CO., LTD
  656, GANGNAM-DAERO; SEOUL, SOUTH KOREA
Notify: UAB NOVAKOPA
Load Port: PORT KLANG (WESTPORT), MALAYSIA (MYPKG)
POD: CALLAO, PERU (PECLL)
Container Count: 4 x 40'HC
Gross Wt (kgs): 22,000 KG
Bill of Lading No.: MEDUUD104332
"""


def _ingest(email_id: str, subject: str, body: str, sender: str, atts: dict[str, str]) -> dict:
    payload = {"email_id": email_id, "from": sender, "subject": subject, "body": body, "provider": "test",
               "attachments": [{"name": n, "content_base64": __import__("base64").b64encode(t.encode()).decode()} for n, t in atts.items()]}
    r = client.post("/webhooks/email", json=payload, headers=SUP)
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------- extraction
def test_extractor_resolves_label_synonyms_with_evidence():
    si = extract_seven_fields(SI_TXT, "si")
    bl = extract_seven_fields(BL_TXT, "bl")
    assert si.port_of_loading.evidence.label_found == "Port of Loading"
    assert bl.port_of_loading.evidence.label_found == "Load Port"
    assert bl.consignee.evidence.label_found == "To the Order of"
    assert si.consignee.original == "MOORIM SP CO., LTD" == bl.consignee.original
    assert si.container_count.normalized == 3 and bl.container_count.normalized == 4
    assert si.gross_weight_kg.normalized == 22000 == bl.gross_weight_kg.normalized
    assert si.shipper.evidence.line == 4 and "Shipper/Exporter" in si.shipper.evidence.snippet


def test_extractor_never_invents_missing_values():
    x = extract_seven_fields("SHIPPING INSTRUCTION\nShipper: X\nCONSIGNEE: \nNotify: Y\nPOL: A\nPOD: B\nContainers: TBA\nGross Weight: ???\n", "si")
    assert x.consignee.normalized is None and x.consignee.needs_review
    assert x.container_count.normalized is None and x.gross_weight_kg.normalized is None


def test_readers_flag_unreadable_and_empty():
    assert read_document("x.pdf", b"").status.value == "EMPTY"
    assert read_document("x.pdf", b"%PDF-1.5\n\x00\x01garbage").status.value == "UNREADABLE"
    assert read_document("x.exe", b"MZ").status.value == "UNSUPPORTED"
    assert read_document("x.txt", b"hello").status.value == "EXTRACTED"


# ---------------------------------------------------------------- security
def test_spam_detected_and_untrusted_attachment_not_executed():
    from datetime import datetime

    from app.contracts.schemas import AttachmentMeta

    e = EmailMessage(id="spam1", sender="winner@prize-claims.info", subject="Congratulations! You have WON a $1,000 Gift Card - CLAIM NOW",
                     body="Click here to claim your gift card now: http://bit.ly/claim-prize-now", received_at=datetime.utcnow())
    a = AttachmentMeta(id="a1", source_email_id="spam1", file_name="invoice.exe", file_type="exe")
    sec = assess_security(e, [a])
    assert sec.outcome == SecurityOutcome.SECURITY_REVIEW  # blocked attachment type is CRITICAL
    assert any(s.signal == "BLOCKED_ATTACHMENT_TYPE" for s in sec.signals)
    sec2 = assess_security(e, [])
    assert sec2.outcome == SecurityOutcome.SPAM


# ---------------------------------------------------------------- E2E: email -> case -> compare -> draft -> review -> notify -> audit -> complete
def test_e2e_mismatch_flow_with_notify_party_and_audit():
    case = _ingest("e2e_001", "TO CONFIRM DOCS _ 5RSG-00133 _ CALLAO_PERU _ MOORIM SP CO., LTD _ MEDUUD104332",
                   "Hi,\n\nAttached are the SI and draft BL for OC 5RSG-00133. Please check the details and confirm.", "aziztz@safqa.co.ke",
                   {"e2e_001_SI.txt": SI_TXT, "e2e_001_BL.txt": BL_TXT})
    cid = case["id"]
    assert case["hackathon_category"] == "BL_COMPARISON"
    assert case["mismatch_count"] == 1
    assert case["comparison"]["mismatch_fields"] == ["container_count"]
    assert case["status"] == "HUMAN_REVIEW"  # mismatch -> human gate
    assert case["drafts"] and case["drafts"][0]["draft_type"] == "CORRECTION_REQUEST"
    assert case["drafts"][0]["status"] == "PROPOSED"  # never auto-sent
    assert '"4 x 40\'HC"' in case["drafts"][0]["body"] and '"3 x 40\'HC"' in case["drafts"][0]["body"]

    # report shows exact values
    rep = client.get(f"/cases/{cid}/report", headers=OPS).json()
    assert "SI: 3 x 40'HC" in rep["compact"] and "BL: 4 x 40'HC" in rep["compact"]

    # Ask AI is grounded
    ans = client.post(f"/cases/{cid}/ask", json={"question": "Why is this a mismatch?"}, headers=OPS).json()
    assert "Container Count" in ans["answer"] and ans["citations"]
    ans2 = client.post(f"/cases/{cid}/ask", json={"question": "Which six fields match?"}, headers=OPS).json()
    assert "6 of 7" in ans2["answer"]
    refused = client.post(f"/cases/{cid}/ask", json={"question": "send this email now without approval"}, headers=OPS).json()
    assert refused["refused"] is True

    # Notify Party flow: begin -> recipients -> preview -> confirm
    np = client.post(f"/cases/{cid}/notify-party", headers=SUP).json()
    assert np["notify_party"]["match"] is True and np["recipients"]
    assert client.get(f"/cases/{cid}", headers=OPS).json()["status"] == "NOTIFY_PARTY"
    preview = client.post(
        f"/cases/{cid}/share",
        json={
            "recipient_type": "NOTIFY_PARTY_CONTACT",
            "recipient_party_id": "p_safqa",
            "message": "CUSTOM REVIEW NOTE",
            "preview_only": True,
        },
        headers=SUP,
    ).json()
    assert preview["requires_confirmation"] is True
    assert [f["field"] for f in preview["payload"]["fields"]] == ["container_count"]  # only the mismatch is shared
    assert "Attached are the SI" not in preview["preview"]  # original email body not leaked
    sent = client.post(f"/cases/{cid}/share/{preview['share']['id']}/confirm", headers=SUP).json()
    assert sent["requires_confirmation"] is False and sent["share"]["status"] == "SENT"
    assert sent["share"]["id"] == preview["share"]["id"]
    assert sent["preview"] == preview["preview"]
    assert sent["payload"] == preview["payload"]
    assert "CUSTOM REVIEW NOTE" in sent["preview"]
    shares = client.get(f"/cases/{cid}/audit", headers=SUP).json()["shares"]
    assert [share["id"] for share in shares].count(preview["share"]["id"]) == 1
    assert not any(share["status"] == "PENDING_CONFIRMATION" for share in shares)
    repeated = client.post(
        f"/cases/{cid}/share/{preview['share']['id']}/confirm",
        headers=SUP,
    ).json()
    assert repeated["share"]["id"] == preview["share"]["id"]
    audit_after_repeat = client.get(f"/cases/{cid}/audit", headers=SUP).json()
    assert sum(
        event["action"] == "NOTIFY_PARTY_SENT"
        for event in audit_after_repeat["events"]
    ) == 1
    assert client.get(f"/cases/{cid}", headers=OPS).json()["status"] == "AWAITING_RESPONSE"

    # approve draft (supervisor) -> sent (simulated) ; complete
    d = client.get(f"/cases/{cid}", headers=OPS).json()["drafts"][0]
    ok = client.post(f"/cases/{cid}/approve", json={"draft_id": d["id"]}, headers=SUP)
    assert ok.status_code == 200 and ok.json()["drafts"][0]["status"] == "SENT"
    done = client.post(f"/cases/{cid}/complete", json={"note": "done"}, headers=OPS).json()
    assert done["status"] == "COMPLETED"

    # audit trail contains every step
    audit = client.get(f"/cases/{cid}/audit", headers=SUP).json()
    actions = [e["action"] for e in audit["events"]]
    for expected in ["CASE_CREATED", "SECURITY_CLASSIFIED", "INTENT_CLASSIFIED", "ATTACHMENT_CLASSIFIED", "EXTRACTION_COMPLETED", "COMPARISON_STARTED",
                     "FIELD_RESULT", "MISMATCH_DETECTED", "POLICY_APPLIED", "DRAFT_GENERATED", "NOTIFY_PARTY_STARTED", "SHARE_CREATED", "NOTIFY_PARTY_SENT",
                     "DRAFT_APPROVED", "NOTIFICATION_SENT", "COMPLETED", "STATUS_CHANGED", "ASK_AI"]:
        assert expected in actions, f"missing audit action {expected}"
    assert len(audit["shares"]) >= 1 and audit["shares"][-1]["status"] == "SENT"


def test_all_match_gives_no_mismatch_message_and_confirmation_draft():
    bl_ok = BL_TXT.replace("Container Count: 4 x 40'HC", "Container Count: 3 x 40'HC")
    case = _ingest("e2e_002", "REQUEST BL DRAFT _ PO 26067_ COATED IVORY BOARD__138MT", "Attached are the SI and draft BL. Please check and confirm.", "docs@vitalsolutions.sg",
                   {"e2e_002_SI.txt": SI_TXT, "e2e_002_BL.txt": bl_ok})
    assert case["comparison"]["message"] == "No mismatch detected."
    assert case["mismatch_count"] == 0 and case["status"] == "DRAFT_READY"
    assert case["drafts"][0]["draft_type"] == "CONFIRMATION"


def test_explicit_empty_external_share_discloses_no_fields():
    case = _ingest(
        "share_empty_001",
        "TO CONFIRM DOCS _ EMPTY DISCLOSURE",
        "Attached documents are for the explicit-empty disclosure test.",
        "docs@vitalsolutions.sg",
        {"share_empty_SI.txt": SI_TXT, "share_empty_BL.txt": BL_TXT},
    )
    cid = case["id"]

    preview = client.post(
        f"/cases/{cid}/share",
        json={
            "recipient_type": "NOTIFY_PARTY_CONTACT",
            "recipient_party_id": "p_safqa",
            "include_fields": [],
            "preview_only": True,
        },
        headers=SUP,
    )

    assert preview.status_code == 200
    body = preview.json()
    assert body["payload"]["fields"] == []
    assert "Container Count:" not in body["preview"]
    assert "3 x 40'HC" not in body["preview"]
    assert "4 x 40'HC" not in body["preview"]


def test_concurrent_share_confirmation_emits_one_send_event():
    class DetachedShareRepository(MemoryRepository):
        """Model database reads by returning separate record instances."""

        def __init__(self):
            super().__init__()
            self.confirm_barrier: threading.Barrier | None = None
            self.confirm_reads = 0

        def get_share(self, share_id):
            with self._lock:
                share = self.shares.get(share_id)
                detached = share.model_copy(deep=True) if share else None
                barrier = self.confirm_barrier
                if barrier is not None:
                    self.confirm_reads += 1
                    if self.confirm_reads == barrier.parties:
                        self.confirm_barrier = None
            if barrier is not None:
                barrier.wait()
            return detached

    repo = DetachedShareRepository()
    repo.load_file(ROOT / "supabase" / "seed" / "snapshot.json")
    service = CaseService(repo)
    supervisor = repo.get_user("u_sup_1")
    assert supervisor is not None
    preview = service.share(
        "case_email_004",
        ShareRequest(
            recipient_type=RecipientType.NOTIFY_PARTY_CONTACT,
            recipient_party_id="p_safqa",
            preview_only=True,
        ),
        supervisor,
    )
    share_id = preview["share"]["id"]
    before = sum(
        event.action == "NOTIFY_PARTY_SENT"
        for event in repo.list_audit("case_email_004")
    )

    repo.confirm_barrier = threading.Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: service.confirm_share(
                    "case_email_004",
                    share_id,
                    supervisor,
                ),
                range(2),
            )
        )

    after = sum(
        event.action == "NOTIFY_PARTY_SENT"
        for event in repo.list_audit("case_email_004")
    )
    assert after - before == 1
    assert {result["share"]["id"] for result in results} == {share_id}
    assert {result["share"]["status"] for result in results} == {"SENT"}


def test_supabase_share_confirmation_uses_conditional_status_update():
    class FakeQuery:
        def __init__(self):
            self.payload = None
            self.filters = []

        def update(self, payload):
            self.payload = payload
            return self

        def eq(self, field, value):
            self.filters.append((field, value))
            return self

        def execute(self):
            return SimpleNamespace(
                data=[
                    {
                        "id": "share_atomic_1",
                        "case_id": "case_email_004",
                        "shared_by": "u_sup_1",
                        "recipient_type": "NOTIFY_PARTY_CONTACT",
                        "recipient_party_id": "p_safqa",
                        "recipient_label": "SAFQA LIMITED",
                        "is_external": True,
                        "message": "Frozen preview",
                        "payload_preview": {"fields": []},
                        "status": "SENT",
                        "sent_at": self.payload["sent_at"],
                        "tenant_id": "tenant_april",
                    }
                ]
            )

    class FakeClient:
        def __init__(self):
            self.query = FakeQuery()

        def table(self, name):
            assert name == "shares"
            return self.query

    repo = object.__new__(SupabaseRepository)
    repo.client = FakeClient()
    repo.tenant = "tenant_april"
    sent_at = datetime(2026, 9, 20, 12, 0, 0)

    share = repo.mark_share_sent_if_pending("share_atomic_1", sent_at)

    assert share is not None and share.status == "SENT"
    assert repo.client.query.payload == {
        "status": "SENT",
        "sent_at": sent_at.isoformat(),
    }
    assert repo.client.query.filters == [
        ("id", "share_atomic_1"),
        ("tenant_id", "tenant_april"),
        ("status", "PENDING_CONFIRMATION"),
    ]


def test_missing_bl_waits_for_documents_then_upload_recovers():
    case = _ingest("e2e_003", "TO CONFIRM DOCS _ 5AKR-00230 _ KOPER_SLOVENIA", "Please compare the SI and draft BL and confirm (the draft BL is still missing).", "docs@vitalsolutions.sg",
                   {"e2e_003_SI.txt": SI_TXT})
    assert case["status"] == "WAITING_DOCUMENTS" and case["review_reason"] == "missing_attachment"
    assert any(e["category"] == "MISSING_BL" for e in case["errors"])
    assert case["recommendation"]["action_type"] == "REQUEST_MISSING_DOCUMENT"
    r = client.post(f"/cases/{case['id']}/upload", files={"file": ("e2e_003_BL.txt", BL_TXT.encode(), "text/plain")}, data={"kind": "BL"}, headers=OPS)
    assert r.status_code == 200
    assert r.json()["mismatch_count"] == 1 and r.json()["comparison"]["mismatch_fields"] == ["container_count"]


def test_wrong_doc_type_escalates_without_inventing_mismatch():
    inv = "COMMERCIAL INVOICE\n====\nInvoice No.: 1\nSeller: APRIL\nBuyer: X\nTotal Amount: USD 4,500.00\n*** THIS IS A COMMERCIAL INVOICE - NOT A SHIPPING INSTRUCTION ***\n"
    case = _ingest("e2e_004", "TO CONFIRM DOCS _ 5RSG-51584 _ HOUSTON_US", "Please find attached the SI and the Commercial Invoice. Kindly confirm the BL is in order.", "sales@roxcel.at",
                   {"e2e_004_SI.txt": SI_TXT, "e2e_004_BL.txt": inv})
    assert case["review_reason"] == "wrong_doc_type" and case["mismatch_count"] == 0
    assert any(a["signal"] == "UNEXPECTED_DOCUMENT_TYPE" for a in case["anomalies"])


def test_informational_email_is_no_reply_needed():
    case = _ingest("e2e_005", "_RPA_ India HSS SD Billing Process Completed - LE HAVRE V.QI540A", "This is an automated notification. The process has completed successfully. No action required.\n\n-- RPA Bot", "rpa.bot@aprilasia.com", {})
    assert case["status"] == "NO_ACTION_INFO" and case["action_required"] is False and case["drafts"] == []
    assert case["recommendation"]["action_type"] == "NO_ACTION_INFORMATION"


def test_duplicate_message_does_not_create_second_case():
    a = _ingest("dup_001", "TO CONFIRM DOCS _ DUP", "Attached are the SI and draft BL. Please confirm.", "docs@vitalsolutions.sg", {"dup_001_SI.txt": SI_TXT, "dup_001_BL.txt": BL_TXT})
    b = client.post("/webhooks/email", json={"email_id": "dup_002", "from": "docs@vitalsolutions.sg", "subject": "TO CONFIRM DOCS _ DUP", "body": "Attached are the SI and draft BL. Please confirm.", "attachments": []}, headers=SUP).json()
    assert b.get("duplicate_of") == "dup_001" and b["case"]["id"] == a["id"]


# ---------------------------------------------------------------- RBAC
def test_unauthorized_share_is_blocked_and_audited():
    case = _ingest("rbac_001", "TO CONFIRM DOCS _ RBAC", "Attached are the SI and draft BL. Please confirm.", "docs@vitalsolutions.sg", {"rbac_001_SI.txt": SI_TXT, "rbac_001_BL.txt": BL_TXT})
    cid = case["id"]
    r = client.post(f"/cases/{cid}/share", json={"recipient_type": "NOTIFY_PARTY_CONTACT", "recipient_party_id": "p_safqa", "confirm_external": True}, headers=OPS)
    assert r.status_code == 403  # operations staff cannot notify external party
    audit = client.get(f"/cases/{cid}/audit", headers=SUP).json()
    assert any(e["action"] == "SHARE_DENIED" for e in audit["events"])
    r2 = client.post(f"/cases/{cid}/share", json={"recipient_type": "INTERNAL_USER", "recipient_user_id": "u_sup_1"}, headers=OPS)
    assert r2.status_code == 200  # internal share allowed
    r3 = client.post(f"/cases/{cid}/share", json={"recipient_type": "NOTIFY_PARTY_CONTACT", "recipient_party_id": "p_novakopa", "confirm_external": True}, headers=SUP)
    assert r3.status_code == 403  # unapproved party contact


def test_auditor_is_read_only_and_policy_needs_admin():
    assert client.get("/cases", headers=AUD).status_code == 200
    assert client.post("/cases/batch", json={"action": "archive", "case_ids": []}, headers=AUD).status_code == 403
    assert client.put("/policies", json={"human_review": {"confidence_below": 0.9}}, headers=OPS).status_code == 403
    r = client.put("/policies", json={"human_review": {"confidence_below": 0.9}, "change_note": "tighten"}, headers={"X-User-Id": "u_admin_1"})
    assert r.status_code == 200 and r.json()["active"]["version"] == "v2"
    assert client.get("/policies", headers=OPS).json()["effective"]["human_review"]["confidence_below"] == 0.9


def test_batch_requires_confirmation_for_drafts_and_metrics_work():
    ids = [c["id"] for c in client.get("/cases", headers=SUP).json()["items"][:2]]
    r = client.post("/cases/batch", json={"action": "draft", "case_ids": ids}, headers=SUP).json()
    assert r["requires_confirmation"] is True
    m = client.get("/dashboard/metrics", headers=OPS).json()
    assert m["incoming_emails"] >= 5 and "avg_processing_ms" in m


@pytest.mark.skipif(not (BUNDLE / "inbox").exists(), reason="bundle not present")
def test_bundle_sample_email_004_two_mismatches():
    raw = json.loads((BUNDLE / "inbox" / "email_004.json").read_text(encoding="utf-8"))
    r = client.post("/webhooks/email", json={**raw, "email_id": "bundle_004"}, headers=SUP).json()
    r = r.get("case", r)  # duplicate-detection envelope if another test already ingested this email
    assert sorted(r["comparison"]["mismatch_fields"]) == ["consignee", "notify_party"]
