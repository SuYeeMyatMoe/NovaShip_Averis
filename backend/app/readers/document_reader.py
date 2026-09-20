"""
Attachment readers. Content is NEVER executed - only parsed to text.

Returns a ReadResult with raw text, page count, status and a safe error note.

  text families   .txt .md .csv .tsv .html .htm .eml .rtf          (standard library)
  office          .docx .xlsx (python-docx / openpyxl)  .xls (xlrd)  .doc (heuristic text runs)
  pdf             text layer via pypdf; image-only pages -> OCR
  images          .png .jpg .jpeg .webp .gif .bmp .tif .tiff        -> OCR

OCR (`ocr_enabled()`): OCR_ENABLED=1 forces it, OCR_ENABLED=0 disables it, unset/`auto` turns it on
when GOOGLE_API_KEY is present. Gemini vision is tried first, then pytesseract. OCR text is always
tagged "lower confidence" (AttachmentMeta.ocr, confidence capped by the pipeline) and is never used
to decide MATCH/MISMATCH directly: values still need the extractor's literal snippet, and
low-confidence fields route to human review.
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

TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".tsv", ".html", ".htm", ".eml", ".rtf"}
OFFICE_EXTENSIONS = {".docx", ".xlsx", ".xls", ".doc"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | OFFICE_EXTENSIONS | IMAGE_EXTENSIONS | {".pdf"}
BLOCKED_EXTENSIONS = {".exe", ".bat", ".cmd", ".js", ".vbs", ".scr", ".msi", ".ps1", ".jar", ".com", ".dll"}
IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp", ".tif": "image/tiff", ".tiff": "image/tiff"}
OCR_NOTE = "Text recovered via OCR (lower confidence)."


def ocr_enabled() -> bool:
    """OCR_ENABLED=1 on, 0 off; unset/auto follows the presence of GOOGLE_API_KEY (Gemini vision)."""
    raw = os.environ.get("OCR_ENABLED", "auto").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(os.environ.get("GOOGLE_API_KEY", "").strip())


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
        if ext in {".txt", ".md"}:
            text = _decode_text(data)
            res.page_count = 1
        elif ext in {".csv", ".tsv"}:
            text = _read_delimited(data, "\t" if ext == ".tsv" else None)
            res.page_count = 1
        elif ext in {".html", ".htm"}:
            text = _read_html(data)
            res.page_count = 1
        elif ext == ".eml":
            text, res.note = _read_eml(data)
            res.page_count = 1
        elif ext == ".rtf":
            text = _read_rtf(data)
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
        elif ext == ".xls":
            text = _read_xls(data)
            res.page_count = 1
        elif ext == ".doc":
            text, res.note = _read_doc(data)
            res.page_count = 1
        elif ext in IMAGE_EXTENSIONS:
            text, res.note = _read_image(ext, data)
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
        if ext in IMAGE_EXTENSIONS or ext == ".pdf":
            res.note = res.note or ("No text could be recovered from the image." if ocr_enabled() else "No extractable text layer (scanned image?). OCR is off: set OCR_ENABLED=1 or a GOOGLE_API_KEY.")
        else:
            res.note = res.note or "No readable text found in the file."
    else:
        res.status = ExtractionStatus.EXTRACTED
    return res


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# text families
def _read_delimited(data: bytes, delimiter: Optional[str]) -> str:
    import csv

    text = _decode_text(data)
    sample = text[:4096]
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
    out: list[str] = []
    for row in csv.reader(io.StringIO(text), delimiter=delimiter):
        vals = [v.strip() for v in row]
        if not any(vals):
            continue
        if len(vals) >= 2 and vals[0] and not any(vals[2:]):
            out.append(f"{vals[0]}: {vals[1]}")
        else:
            out.append(" | ".join(v for v in vals if v))
    return "\n".join(out)


def _read_html(data: bytes) -> str:
    from app.readers.textutil import strip_html

    return strip_html(_decode_text(data))


def _read_eml(data: bytes) -> tuple[str, Optional[str]]:
    """Headers plus the text body of a forwarded .eml. Nested attachments are listed, not parsed."""
    from email import policy as email_policy
    from email.parser import BytesParser

    from app.readers.textutil import strip_html

    msg = BytesParser(policy=email_policy.default).parsebytes(data)
    out = [f"{header}: {msg.get(header, '')}" for header in ("From", "To", "Cc", "Date", "Subject") if msg.get(header)]
    out.append("")
    body = msg.get_body(preferencelist=("plain", "html"))
    if body is not None:
        content = body.get_content()
        out.append(strip_html(content) if body.get_content_type() == "text/html" else content)
    nested = [part.get_filename() for part in msg.iter_attachments() if part.get_filename()]
    note = f"Nested attachments not parsed: {', '.join(nested)}" if nested else None
    return "\n".join(out), note


def _read_rtf(data: bytes) -> str:
    from app.readers.textutil import rtf_to_text

    return rtf_to_text(data.decode("cp1252", errors="replace"))


# ---------------------------------------------------------------------------
# legacy office
def _read_xls(data: bytes) -> str:
    import xlrd

    book = xlrd.open_workbook(file_contents=data)
    out: list[str] = []
    for sheet in book.sheets():
        out.append(f"[[SHEET {sheet.name}]]")
        for r in range(sheet.nrows):
            vals = ["" if v is None else str(v).strip() for v in sheet.row_values(r)]
            vals = [v[:-2] if v.endswith(".0") and v[:-2].isdigit() else v for v in vals]
            if not any(vals):
                continue
            if len(vals) >= 2 and vals[0] and not any(vals[2:]):
                out.append(f"{vals[0]}: {vals[1]}")
            else:
                out.append(" | ".join(v for v in vals if v))
    return "\n".join(out)


def _read_doc(data: bytes) -> tuple[str, Optional[str]]:
    """Legacy Word binary: no clean parser exists, so recover printable runs (WordDocument stream first when olefile is available)."""
    from app.readers.textutil import printable_runs

    payload = data
    try:
        import olefile

        if olefile.isOleFile(data):
            ole = olefile.OleFileIO(data)
            if ole.exists("WordDocument"):
                payload = ole.openstream("WordDocument").read()
    except Exception:
        payload = data
    text = printable_runs(payload)
    if len(text.strip()) < 40:
        text = printable_runs(data)
    if len(text.strip()) < 40:
        return "", "Legacy .doc yielded too little text; ask for DOCX or PDF."
    return text, "Legacy .doc read heuristically (lower confidence); confirm values against the original."


# ---------------------------------------------------------------------------
# images
def _read_image(ext: str, data: bytes) -> tuple[str, Optional[str]]:
    if not ocr_enabled():
        return "", "Image attachment; OCR is off (set OCR_ENABLED=1 or GOOGLE_API_KEY)."
    text = _ocr_images([(IMAGE_MIME.get(ext, "image/png"), data)])
    return text, (OCR_NOTE if text.strip() else None)


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
        if ocr_enabled():
            ocr = _ocr_pdf(data)
            if ocr.strip():
                return ocr, len(reader.pages), OCR_NOTE
    return text, len(reader.pages), note


def _pdf_page_images(data: bytes) -> list[tuple[str, bytes]]:
    """Raster images embedded in each page (image-only scans embed one per page). Empty when pypdf cannot decode them."""
    from pypdf import PdfReader

    out: list[tuple[str, bytes]] = []
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        for page in reader.pages:
            for image in getattr(page, "images", []) or []:
                name = (getattr(image, "name", "") or "").lower()
                mime = "image/jpeg" if name.endswith((".jpg", ".jpeg")) else "image/png"
                blob = getattr(image, "data", b"")
                if blob:
                    out.append((mime, blob))
                    break  # one raster per page is enough for OCR
    except Exception:
        return []
    return out


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
    """Gemini vision OCR of a scanned PDF: embedded page rasters first, then the PDF itself as a media part."""
    if not os.environ.get("GOOGLE_API_KEY"):
        return ""
    pages = _pdf_page_images(data)
    if pages:
        text = _ocr_images(pages)
        if text.strip():
            return text
    return _ocr_images([("application/pdf", data)])


def _gemini_vision_model():
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(
        model=os.environ.get("GEMINI_OCR_MODEL", os.environ.get("GEMINI_CHAT_MODEL", "gemini-3.6-flash")),
        google_api_key=os.environ["GOOGLE_API_KEY"],
        temperature=0,
    )


_OCR_PROMPT = "Extract all visible text from this scanned shipping document. Return plain text only, keep the reading order and one field per line. Do not invent values that are not visible."


def vision_ocr_allowed() -> bool:
    """Policy switch `ai_privacy.allow_vision_ocr`: scanned pages are the one thing that cannot be masked before leaving the desk."""
    try:
        from app.ai.privacy import privacy_settings

        return privacy_settings()["allow_vision_ocr"]
    except Exception:
        return True


def _ocr_images(images: list[tuple[str, bytes]]) -> str:
    """Gemini vision OCR for image blobs (or a whole PDF as a media part). Never used for MATCH/MISMATCH. Empty string on any failure."""
    if not os.environ.get("GOOGLE_API_KEY") or not images or not vision_ocr_allowed():
        return ""
    try:
        import base64

        from langchain_core.messages import HumanMessage

        model = _gemini_vision_model()
        out: list[str] = []
        for index, (mime, blob) in enumerate(images):
            b64 = base64.b64encode(blob).decode("ascii")
            part = ({"type": "media", "mime_type": mime, "data": b64} if mime == "application/pdf"
                    else {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
            resp = model.invoke([HumanMessage(content=[{"type": "text", "text": _OCR_PROMPT}, part])])
            text = getattr(resp, "content", "") or ""
            if isinstance(text, list):
                text = " ".join(str(p.get("text", p) if isinstance(p, dict) else p) for p in text)
            text = str(text).strip()
            if text:
                out.append(f"[[PAGE {index + 1}]]\n{text}" if len(images) > 1 else text)
        return "\n".join(out).strip()
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
