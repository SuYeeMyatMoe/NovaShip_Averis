"""Small text helpers shared by the attachment readers and the mail connector. Pure standard library."""
from __future__ import annotations

import re
from html.parser import HTMLParser


class _TextHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style"}:
            self._ignored_depth += 1
        elif not self._ignored_depth and tag in {"br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4", "table"}:
            self.text.append("\n")
        elif not self._ignored_depth and tag in {"td", "th"}:
            self.text.append(" | ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._ignored_depth:
            self._ignored_depth -= 1
        elif not self._ignored_depth and tag in {"p", "div", "tr", "li", "h1", "h2", "h3", "h4"}:
            self.text.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self.text.append(data)


def strip_html(html: str) -> str:
    parser = _TextHTMLParser()
    parser.feed(html)
    text = "".join(parser.text)
    text = re.sub(r"[ \t]*\|[ \t]*(?=\n)", "", text)          # trailing cell separators
    text = re.sub(r"(?m)^[ \t]*\|[ \t]*", "", text)           # leading cell separators
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()


_RTF_CONTROL = re.compile(r"\\([a-z]+)(-?\d+)? ?|\\'([0-9a-f]{2})|\\([^a-z])", re.I)


def rtf_to_text(rtf: str) -> str:
    """Strip RTF control words; keeps paragraph breaks and hex-escaped characters. Groups with destinations (fonts, colours, pictures) are dropped."""
    out: list[str] = []
    depth = 0
    skip_depth: int | None = None
    i = 0
    n = len(rtf)
    while i < n:
        ch = rtf[i]
        if ch == "{":
            depth += 1
            if rtf.startswith("{\\*", i) or re.match(r"\{\\(fonttbl|colortbl|stylesheet|info|pict|header|footer|generator)", rtf[i:i + 14]):
                if skip_depth is None:
                    skip_depth = depth
            i += 1
            continue
        if ch == "}":
            if skip_depth is not None and depth == skip_depth:
                skip_depth = None
            depth -= 1
            i += 1
            continue
        if ch == "\\":
            m = _RTF_CONTROL.match(rtf, i)
            if not m:
                i += 1
                continue
            word, hexchar, symbol = m.group(1), m.group(3), m.group(4)
            if skip_depth is None:
                if word in {"par", "line"}:
                    out.append("\n")
                elif word in {"tab", "cell"}:
                    out.append("\t" if word == "tab" else " | ")
                elif word == "row":
                    out.append("\n")
                elif hexchar:
                    out.append(bytes.fromhex(hexchar).decode("cp1252", errors="replace"))
                elif symbol in {"\\", "{", "}"}:
                    out.append(symbol)
                elif symbol == "~":
                    out.append(" ")
            i = m.end()
            continue
        if skip_depth is None and ch not in "\r\n":
            out.append(ch)
        i += 1
    text = "".join(out)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()


_PRINTABLE_CP1252 = re.compile(rb"[\x20-\x7e\x80-\xff\t]{4,}")
_PRINTABLE_UTF16 = re.compile(rb"(?:[\x20-\x7e\t]\x00){4,}")


def printable_runs(data: bytes, min_len: int = 4) -> str:
    """Best-effort text from a binary blob (legacy .doc): UTF-16LE runs first, then cp1252 runs. Lower confidence by nature."""
    runs: list[str] = []
    for m in _PRINTABLE_UTF16.finditer(data):
        text = m.group(0).decode("utf-16le", errors="ignore").strip()
        if len(text) >= min_len:
            runs.append(text)
    if not runs:
        for m in _PRINTABLE_CP1252.finditer(data):
            text = m.group(0).decode("cp1252", errors="ignore").strip()
            if len(text) >= min_len and sum(c.isalnum() or c.isspace() for c in text) / max(len(text), 1) > 0.7:
                runs.append(text)
    text = "\n".join(runs)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    text = text.replace("\r", "\n").replace("\x07", "\n")   # Word cell/row marks
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()
