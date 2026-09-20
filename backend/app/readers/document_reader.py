"""
Attachment readers. Content is NEVER executed - only parsed to text.

Returns a ReadResult with raw text, page count, status and a safe error note.
Supported: .txt .pdf (text layer) .docx .xlsx  |  image-only PDFs -> UNREADABLE
(OCR: OCR_ENABLED=1 tries Gemini vision then pytesseract; still UNREADABLE if both fail).
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from app.contracts.schemas import ExtractionStatus

logging.getLogger("pypdf").setLevel(logging.ERROR)

SUPPORTED_EXTENSIONS = {".txt", ".pdf", ".docx", ".xlsx"}
BLOCKED_EXTENSIONS = {".exe", ".bat", ".cmd", ".js", ".vbs", ".scr", ".msi", ".ps1", ".jar", ".com", ".dll"}


@dataclass
class ReadResult:
    text: str = ""
    status: ExtractionStatus = ExtractionStatus.PENDING
    page_count: Optional[int] = None
    note: Optional[str] = None
    lines: list[str] = field(default_factory=list)
    file_type: str = ""
    checksum: str = ""
    size_bytes: int = 0


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_extension(name: str) -> str:
    return os.path.splitext(name)[1].lower()


def read_document(file_name: str, data: bytes) -> ReadResult:
    ext = file_extension(file_name)
    res = ReadResult(file_type=ext.lstrip("."), checksum=sha256(data), size_bytes=len(data))

    if ext in BLOCKED_EXTENSIONS:
        res.status = ExtractionStatus.UNSUPPORTED
        res.note = f"Blocked attachment type '{ext}' (policy: blocked_attachment_types)."
        return res
    if ext not in SUPPORTED_EXTENSIONS:
        res.status = ExtractionStatus.UNSUPPORTED
        res.note = f"Unsupported attachment type '{ext}'."
        return res
    if len(data) == 0:
        res.status = ExtractionStatus.EMPTY
        res.note = "File is empty (0 bytes)."
        return res

    try:
        if ext == ".txt":
            text = data.decode("utf-8", errors="replace")
            res.page_count = 1
        elif ext == ".pdf":
            text, res.page_count, note = _read_pdf(data)
            if note:
                res.note = note
        elif ext == ".docx":
            text = _read_docx(data)
            res.page_count = 1
        elif ext == ".xlsx":
            text = _read_xlsx(data)
            res.page_count = 1
        else:  # pragma: no cover
            text = ""
    except Exception as exc:  # corrupt / garbled file
        res.status = ExtractionStatus.UNREADABLE
        res.note = f"Parser failed: {type(exc).__name__}. File may be corrupt or truncated."
        return res

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    res.text = text
    res.lines = text.split("\n")
    if not text.strip():
        res.status = ExtractionStatus.UNREADABLE
        res.note = res.note or "No extractable text layer (scanned image?). OCR required."
    else:
        res.status = ExtractionStatus.EXTRACTED
    return res


# ---------------------------------------------------------------------------
def _read_pdf(data: bytes) -> tuple[str, int, Optional[str]]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data), strict=False)
    pages = []
    for i, page in enumerate(reader.pages):
        try:
            t = page.extract_text() or ""
        except Exception:
            t = ""
        if t.strip():
            pages.append(f"[[PAGE {i + 1}]]\n{t}")
    text = "\n".join(pages)
    note = None
    if not text.strip():
        note = "PDF has no text layer (image-only scan)."
        if os.environ.get("OCR_ENABLED") == "1":
            ocr = _ocr_pdf(data)
            if ocr.strip():
                return ocr, len(reader.pages), "Text recovered via OCR (lower confidence)."
    return text, len(reader.pages), note


def _ocr_pdf(data: bytes) -> str:
    gemini = _ocr_pdf_gemini(data)
    if gemini.strip():
        return gemini
    try:
        from pdf2image import convert_from_bytes
        import pytesseract

        images = convert_from_bytes(data)
        return "\n".join(f"[[PAGE {i+1}]]\n" + pytesseract.image_to_string(im) for i, im in enumerate(images))
    except Exception:
        return ""


def _ocr_pdf_gemini(data: bytes) -> str:
    """Gemini vision OCR. Never used for MATCH/MISMATCH. Requires OCR_ENABLED=1 and GOOGLE_API_KEY."""
    if not os.environ.get("GOOGLE_API_KEY"):
        return ""
    try:
        import base64

        from langchain_core.messages import HumanMessage
        from langchain_google_genai import ChatGoogleGenerativeAI

        model = ChatGoogleGenerativeAI(
            model=os.environ.get("GEMINI_OCR_MODEL", os.environ.get("GEMINI_CHAT_MODEL", "gemini-2.0-flash")),
            google_api_key=os.environ["GOOGLE_API_KEY"],
            temperature=0,
        )
        b64 = base64.b64encode(data).decode("ascii")
        msg = HumanMessage(content=[
            {"type": "text", "text": "Extract all visible text from this scanned shipping document. Return plain text only. Do not invent values that are not visible."},
            {"type": "image_url", "image_url": {"url": f"data:application/pdf;base64,{b64}"}},
        ])
        resp = model.invoke([msg])
        text = getattr(resp, "content", "") or ""
        if isinstance(text, list):
            text = " ".join(str(part) for part in text)
        return str(text).strip()
    except Exception:
        return ""


def _read_docx(data: bytes) -> str:
    from docx import Document

    doc = Document(io.BytesIO(data))
    out: list[str] = []
    for p in doc.paragraphs:
        if p.text.strip():
            out.append(p.text)
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip().replace("\n", "; ") for c in row.cells]
            if len(cells) >= 2:
                out.append(f"{cells[0]}: {cells[1]}")
            else:
                out.append(" | ".join(cells))
    return "\n".join(out)


def _read_xlsx(data: bytes) -> str:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    out: list[str] = []
    for ws in wb.worksheets:
        out.append(f"[[SHEET {ws.title}]]")
        for row in ws.iter_rows(values_only=True):
            vals = ["" if v is None else str(v) for v in row]
            if not any(v.strip() for v in vals):
                continue
            if len(vals) >= 2 and vals[0].strip():
                out.append(f"{vals[0].strip()}: {vals[1].strip()}")
            else:
                out.append(" | ".join(v for v in vals if v.strip()))
    return "\n".join(out)
