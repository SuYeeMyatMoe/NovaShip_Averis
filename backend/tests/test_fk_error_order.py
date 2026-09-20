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
