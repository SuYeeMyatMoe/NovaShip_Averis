"""Every attachment family becomes text: text families, legacy Office, images and scanned PDFs through OCR."""
from __future__ import annotations

import struct
import sys
from types import ModuleType

import pytest

from app.readers import document_reader as dr
from app.readers.document_reader import ocr_enabled, read_document
from app.readers.textutil import printable_runs, rtf_to_text, strip_html

SI_LINES = ["Shipper: APRIL FAR EAST (M) SDN BHD", "Consignee: EAST BRIGHT FZ-LLC", "Port of Loading: NANTONG, CHINA", "Gross Weight (KG): 131,058 KG"]


def _assert_fields(text: str) -> None:
    assert "APRIL FAR EAST" in text and "EAST BRIGHT" in text and "131,058" in text


# ---------------------------------------------------------------- text families
def test_csv_tsv_become_label_value_lines():
    csv_bytes = ("\n".join(line.replace(": ", ",", 1) for line in SI_LINES)).encode()
    res = read_document("si.csv", csv_bytes)
    assert res.status.value == "EXTRACTED" and "Shipper: APRIL FAR EAST (M) SDN BHD" in res.text
    tsv = read_document("si.tsv", ("\n".join(line.replace(": ", "\t", 1) for line in SI_LINES)).encode())
    assert "Consignee: EAST BRIGHT FZ-LLC" in tsv.text
    assert read_document("notes.md", b"# SI\n\nShipper: X").text.startswith("# SI")


def test_html_and_eml_are_stripped_to_text():
    html = "<html><style>p{}</style><body><h1>Shipping Instruction</h1><table><tr><td>Shipper</td><td>APRIL FAR EAST (M) SDN BHD</td></tr></table><p>Gross Weight (KG): 131,058 KG</p><script>x()</script></body></html>"
    res = read_document("si.html", html.encode())
    assert res.status.value == "EXTRACTED" and "Shipper | APRIL FAR EAST (M) SDN BHD" in res.text and "x()" not in res.text and "p{}" not in res.text
    eml = (b"From: docs@example.com\r\nTo: desk@example.com\r\nSubject: SI for PO 1\r\nContent-Type: text/plain\r\n\r\n" + "\n".join(SI_LINES).encode())
    res = read_document("forward.eml", eml)
    assert res.text.startswith("From: docs@example.com") and "Subject: SI for PO 1" in res.text
    _assert_fields(res.text)


def test_rtf_control_words_are_stripped():
    rtf = r"{\rtf1\ansi{\fonttbl{\f0 Arial;}}{\colortbl;\red0\green0\blue0;}\f0\fs20 Shipper: APRIL FAR EAST (M) SDN BHD\par Gross Weight (KG): 131,058 KG\par Consignee: EAST BRIGHT FZ\'2dLLC}"
    text = rtf_to_text(rtf)
    assert "Arial" not in text and "Shipper: APRIL FAR EAST (M) SDN BHD" in text and "EAST BRIGHT FZ-LLC" in text
    assert read_document("si.rtf", rtf.encode()).status.value == "EXTRACTED"


def test_strip_html_keeps_table_rows_readable():
    assert strip_html("<table><tr><th>Port of Loading</th><td>NANTONG</td></tr></table>") == "Port of Loading | NANTONG"


# ---------------------------------------------------------------- legacy office
def _xls_bytes(rows: list[list[str]]) -> bytes:
    """Minimal BIFF8 workbook (BOF, one sheet, LABEL cells) accepted by xlrd."""
    import xlrd  # noqa: F401  (ensures the dependency is present)

    def rec(code: int, body: bytes) -> bytes:
        return struct.pack("<HH", code, len(body)) + body

    def label(r: int, c: int, text: str) -> bytes:
        raw = text.encode("latin-1")
        return rec(0x0204, struct.pack("<HHH", r, c, 0) + struct.pack("<HB", len(raw), 0) + raw)

    sheet = rec(0x0809, struct.pack("<HHHHIH", 0x0600, 0x0010, 0, 0, 0, 0)) + b"".join(label(r, c, v) for r, row in enumerate(rows) for c, v in enumerate(row)) + rec(0x000A, b"")
    globals_ = rec(0x0809, struct.pack("<HHHHIH", 0x0600, 0x0005, 0, 0, 0, 0))
    # BOUNDSHEET points at the sheet BOF: after globals BOF, the BOUNDSHEET record itself and the globals EOF
    name = b"SI"
    boundsheet_body = lambda offset: struct.pack("<IBB", offset, 0, 0) + bytes([len(name), 0]) + name
    offset = len(globals_) + len(rec(0x0085, boundsheet_body(0))) + 4
    boundsheet = rec(0x0085, boundsheet_body(offset))
    stream = globals_ + boundsheet + rec(0x000A, b"") + sheet
    return _ole_container(b"Workbook", stream)


def _ole_container(stream_name: bytes, stream: bytes) -> bytes:
    """Tiny single-stream OLE2 compound file (what .xls/.doc are wrapped in)."""
    import olefile  # noqa: F401

    sector = 512
    data_sectors = -(-len(stream) // sector)
    # FAT: sector 0 = directory, 1..n = stream, last FAT sector
    fat = [0xFFFFFFFE] + [i + 2 for i in range(1, data_sectors)] + [0xFFFFFFFE]
    fat_sector_index = len(fat)
    fat.append(0xFFFFFFFD)
    fat += [0xFFFFFFFF] * (128 - len(fat))
    header = bytearray(512)
    header[0:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    struct.pack_into("<HHHHHHIIIIIIIII", header, 24, 0x003E, 0x0003, 0xFFFE, 9, 6, 0, 0, 0, 1, 0, 4096, 0xFFFFFFFE, 0, 0xFFFFFFFE, 0)
    struct.pack_into("<I", header, 76, fat_sector_index)
    for i in range(1, 109):
        struct.pack_into("<I", header, 76 + i * 4, 0xFFFFFFFF)

    def entry(name: bytes, typ: int, child: int, start: int, size: int) -> bytes:
        uname = name.decode().encode("utf-16le") + b"\x00\x00"
        e = bytearray(128)
        e[0:len(uname)] = uname
        struct.pack_into("<H", e, 64, len(uname))
        e[66] = typ
        e[67] = 1
        struct.pack_into("<iii", e, 68, -1, -1, child)
        struct.pack_into("<II", e, 116, start, size)
        return bytes(e)

    directory = entry(b"Root Entry", 5, 1, 0xFFFFFFFE, 0) + entry(stream_name, 2, -1, 1, len(stream)) + b"\x00" * 256
    body = directory + stream.ljust(data_sectors * sector, b"\x00") + b"".join(struct.pack("<I", v) for v in fat)
    return bytes(header) + body


def test_xls_via_xlrd_and_doc_via_heuristic_runs():
    rows = [line.split(": ", 1) for line in SI_LINES]
    res = read_document("si.xls", _xls_bytes(rows))
    assert res.status.value == "EXTRACTED", res.note
    assert "[[SHEET SI]]" in res.text
    _assert_fields(res.text)

    doc_stream = ("\r".join(SI_LINES) + "\r").encode("utf-16le")
    res = read_document("si.doc", _ole_container(b"WordDocument", b"\xec\xa5\xc1\x00" * 8 + doc_stream))
    assert res.status.value == "EXTRACTED" and "lower confidence" in (res.note or "")
    _assert_fields(res.text)
    assert printable_runs(b"\x00\x01\x02ab") == ""
    tiny = read_document("empty.doc", b"\xd0\xcf\x11\xe0" + b"\x00" * 600)
    assert tiny.status.value == "UNREADABLE"


# ---------------------------------------------------------------- OCR on/off and images
def test_ocr_enabled_follows_env_and_google_key(monkeypatch):
    monkeypatch.setenv("OCR_ENABLED", "1")
    assert ocr_enabled() is True
    monkeypatch.setenv("OCR_ENABLED", "0")
    monkeypatch.setenv("GOOGLE_API_KEY", "k")
    assert ocr_enabled() is False
    monkeypatch.delenv("OCR_ENABLED")
    assert ocr_enabled() is True
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert ocr_enabled() is False


def test_image_attachment_is_ocrd_with_gemini_or_reported_off(monkeypatch):
    monkeypatch.setenv("OCR_ENABLED", "0")
    off = read_document("scan.png", b"\x89PNG fake")
    assert off.status.value == "UNREADABLE" and "OCR is off" in off.note
    monkeypatch.setenv("OCR_ENABLED", "1")
    monkeypatch.setenv("GOOGLE_API_KEY", "not-a-real-key")
    seen: list[tuple[str, int]] = []
    monkeypatch.setattr(dr, "_ocr_images", lambda images: seen.append((images[0][0], len(images))) or "\n".join(SI_LINES))
    res = read_document("scan.JPG", b"\xff\xd8 fake")
    assert res.status.value == "EXTRACTED" and "lower confidence" in res.note and seen == [("image/jpeg", 1)]
    _assert_fields(res.text)
    monkeypatch.setattr(dr, "_ocr_images", lambda images: "")
    empty = read_document("blank.png", b"\x89PNG fake")
    assert empty.status.value == "UNREADABLE" and "No text could be recovered" in empty.note


def test_scanned_pdf_uses_embedded_page_images_then_pdf_media(monkeypatch):
    monkeypatch.setenv("OCR_ENABLED", "1")
    monkeypatch.setenv("GOOGLE_API_KEY", "not-a-real-key")
    calls: list[list[str]] = []

    def fake_ocr(images):
        calls.append([m for m, _ in images])
        return "TEXT FROM " + images[0][0]

    monkeypatch.setattr(dr, "_ocr_images", fake_ocr)
    monkeypatch.setattr(dr, "_pdf_page_images", lambda data: [("image/png", b"raster")])
    assert dr._ocr_pdf_gemini(b"%PDF") == "TEXT FROM image/png"
    monkeypatch.setattr(dr, "_pdf_page_images", lambda data: [])
    assert dr._ocr_pdf_gemini(b"%PDF") == "TEXT FROM application/pdf"
    assert calls == [["image/png"], ["application/pdf"]]
    fake_pdf, fake_tess = ModuleType("pdf2image"), ModuleType("pytesseract")
    fake_pdf.convert_from_bytes = lambda data: (_ for _ in ()).throw(RuntimeError("no poppler"))
    monkeypatch.setitem(sys.modules, "pdf2image", fake_pdf)
    monkeypatch.setitem(sys.modules, "pytesseract", fake_tess)
    monkeypatch.setattr(dr, "_ocr_images", lambda images: "")
    assert dr._ocr_pdf(b"%PDF") == ""


def test_unsupported_and_blocked_types_still_refused():
    assert read_document("tool.exe", b"MZ").status.value == "UNSUPPORTED"
    assert read_document("archive.zip", b"PK").status.value == "UNSUPPORTED"
    assert read_document("empty.csv", b"").status.value == "EMPTY"
