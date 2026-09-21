"""The first processing error of a brand-new case must be stored after the case row exists (Supabase FK)."""
from __future__ import annotations

import os

os.environ.setdefault("REPO_BACKEND", "memory")
os.environ["AUTO_SEED"] = "0"
os.environ["LLM_PROVIDER"] = "none"

from app.repositories.memory import MemoryRepository  # noqa: E402
from app.services.case_service import CaseService  # noqa: E402

BODY = "Dear Team,\n\nPlease compare the SI and draft BL for I756178688 and confirm (the draft BL is still missing). Thank you."
SI = b"SHIPPING INSTRUCTION\nShipper: APRIL FAR EAST (M) SDN BHD\nConsignee: AL GURG STATIONERY LLC\n"


class StrictRepo(MemoryRepository):
    """Mimics processing_errors.case_id -> cases.id: refuses an error whose case is not stored yet."""

    def save_error(self, err):
        assert err.case_id in self.cases, "error saved before its case existed (FK violation on Supabase)"
        super().save_error(err)


def test_processing_error_is_stored_after_its_case_exists():
    repo = StrictRepo()
    service = CaseService(repo)
    raw = {"email_id": "fk-order-1", "from": "docs@vitalsolutions.sg", "subject": "RE_ TO CONFIRM DOCS _ 5AKR-00230", "body": BODY,
           "attachments": ["attachments/email_507_SI.txt"]}
    email = service.pipe.ingest_email(raw, {"attachments/email_507_SI.txt": SI})
    case = service.pipe.run(email)
    assert case.status.value == "WAITING_DOCUMENTS"
    assert case.errors and case.errors[0].category.value == "MISSING_BL"
    assert repo.get_case(case.id) is not None


def test_raw_original_is_served_and_a_failing_blob_store_does_not_break_ingest(monkeypatch):
    """Attachments tab 'Open' reads /documents/{id}/raw; the original must come back byte-for-byte with its media type.
    When the storage bucket is unavailable the case still processes (text parsed from the bytes in hand) and /raw says so."""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.config import get_repo

    client = TestClient(app)
    import base64
    pdf = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"
    payload = {"email_id": "raw-1", "from": "docs@vitalsolutions.sg", "subject": "REQUEST BL DRAFT raw", "body": BODY,
               "attachments": [{"name": "SI_raw.pdf", "content_base64": base64.b64encode(pdf).decode()}]}
    case = client.post("/webhooks/email", json=payload, headers={"X-User-Id": "u_admin_1"}).json()
    case = case.get("case", case)
    att = case["email"]["attachments"][0]
    r = client.get(f"/cases/{case['id']}/documents/{att['id']}/raw", headers={"X-User-Id": "u_admin_1"})
    assert r.status_code == 200 and r.content == pdf and r.headers["content-type"].startswith("application/pdf") and "SI_raw.pdf" in r.headers["content-disposition"]

    repo = get_repo()
    monkeypatch.setattr(type(repo), "save_blob", lambda self, pointer, data: (_ for _ in ()).throw(RuntimeError("bucket missing")))
    payload["email_id"] = "raw-2"; payload["subject"] = "REQUEST BL DRAFT raw two"
    case2 = client.post("/webhooks/email", json=payload, headers={"X-User-Id": "u_admin_1"}).json()
    case2 = case2.get("case", case2)
    att2 = case2["email"]["attachments"][0]
    assert att2["storage_pointer"] is None and att2["extraction_status"] in ("EXTRACTED", "EMPTY", "UNREADABLE")
    r2 = client.get(f"/cases/{case2['id']}/documents/{att2['id']}/raw", headers={"X-User-Id": "u_admin_1"})
    assert r2.status_code == 404 and "not stored" in r2.json()["detail"]["error"]
