"""Filename, path, count, and size guards for untrusted document inputs."""
from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath, PureWindowsPath


class UnsafeUpload(ValueError):
    pass


def max_file_bytes() -> int:
    return int(os.environ.get("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))


def max_attachment_count() -> int:
    return int(os.environ.get("MAX_ATTACHMENT_COUNT", "10"))


def validate_attachment_count(count: int) -> None:
    if count > max_attachment_count():
        raise UnsafeUpload(f"attachment count exceeds limit of {max_attachment_count()}")


def validate_file_size(size: int) -> None:
    if size > max_file_bytes():
        raise UnsafeUpload(f"attachment exceeds maximum size of {max_file_bytes()} bytes")


def safe_filename(value: str) -> str:
    raw = (value or "upload.bin").strip()
    windows = PureWindowsPath(raw)
    posix = PurePosixPath(raw)
    if windows.is_absolute() or windows.drive or posix.is_absolute():
        raise UnsafeUpload("absolute attachment filenames are not allowed")
    if ".." in windows.parts or ".." in posix.parts:
        raise UnsafeUpload("attachment path traversal is not allowed")
    if len(windows.parts) != 1 or len(posix.parts) != 1:
        raise UnsafeUpload("attachment filename must be a basename")
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", raw).strip(" .")
    if not cleaned or cleaned in {".", ".."}:
        raise UnsafeUpload("attachment filename is invalid")
    return cleaned[:180]


def safe_attachment_name(value: str) -> str:
    """Extract a safe basename from a relative connector path such as attachments/x.pdf."""
    raw = (value or "").replace("\\", "/")
    win = PureWindowsPath(value or "")
    rel = PurePosixPath(raw)
    if win.is_absolute() or win.drive or rel.is_absolute() or ".." in rel.parts:
        raise UnsafeUpload("attachment path traversal is not allowed")
    return safe_filename(rel.name)


def resolve_bundle_attachment(bundle_root: Path, supplied: str) -> Path:
    raw = (supplied or "").replace("\\", "/")
    win = PureWindowsPath(supplied or "")
    rel = PurePosixPath(raw)
    if win.is_absolute() or win.drive or rel.is_absolute() or ".." in rel.parts:
        raise UnsafeUpload("bundle attachment path is outside the attachment root")
    parts = list(rel.parts)
    if parts and parts[0].lower() == "attachments":
        parts = parts[1:]
    if not parts:
        raise UnsafeUpload("bundle attachment path is empty")
    root = (bundle_root / "attachments").resolve()
    candidate = root.joinpath(*parts).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise UnsafeUpload("bundle attachment path is outside the attachment root") from exc
    return candidate
