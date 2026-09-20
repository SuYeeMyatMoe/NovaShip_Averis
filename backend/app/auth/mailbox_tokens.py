"""
Encryption for per-user Gmail refresh tokens (Fernet / AES-128-CBC + HMAC).

The key comes from MAILBOX_TOKEN_KEY (a urlsafe base64 32-byte key) or, when that
is empty, is derived from SESSION_SECRET so the demo needs no extra setting.
Rotating either value makes existing mailbox connections unreadable: users then
simply reconnect through Google sign-in.
"""
from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken


class MailboxTokenError(RuntimeError):
    """The stored token cannot be decrypted with the current key."""


def _key() -> bytes:
    configured = os.environ.get("MAILBOX_TOKEN_KEY", "").strip()
    if configured:
        return configured.encode("ascii")
    from app.auth.accounts import session_secret

    digest = hashlib.sha256(f"mailbox-token:{session_secret()}".encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_token(token: str) -> str:
    return Fernet(_key()).encrypt(token.encode("utf-8")).decode("ascii")


def decrypt_token(enc: str) -> str:
    try:
        return Fernet(_key()).decrypt(enc.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise MailboxTokenError("mailbox token cannot be decrypted; reconnect the mailbox") from exc
