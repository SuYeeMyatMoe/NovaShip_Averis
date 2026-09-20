"""One-time local Gmail OAuth grant; saves only the refresh token to an ignored .env.

Run from the repository root after setting GMAIL_CLIENT_ID and
GMAIL_CLIENT_SECRET in the local .env:

    python backend/scripts/gmail_authorize.py

The API runtime never invokes this interactive helper.
"""
from __future__ import annotations

import argparse
import os
import re
import tempfile
from pathlib import Path

from dotenv import dotenv_values
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


def _save_refresh_token(env_path: Path, refresh_token: str) -> None:
    original = env_path.read_text(encoding="utf-8")
    replacement = f"GMAIL_REFRESH_TOKEN={refresh_token}"
    pattern = re.compile(r"(?m)^GMAIL_REFRESH_TOKEN=.*$")
    updated = pattern.sub(replacement, original, count=1)
    if updated == original:
        updated = original.rstrip("\r\n") + "\n" + replacement + "\n"
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=env_path.parent, delete=False) as handle:
            handle.write(updated)
            temp_name = handle.name
        os.replace(temp_name, env_path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def main() -> None:
    default_env = Path(__file__).resolve().parents[2] / ".env"
    parser = argparse.ArgumentParser(description="Authorize NovaShip Gmail access once and update the local ignored .env.")
    parser.add_argument("--env-file", type=Path, default=default_env)
    args = parser.parse_args()
    env_path = args.env_file.expanduser().resolve()
    if not env_path.is_file():
        raise SystemExit(f"Local env file does not exist: {env_path}. Create it from .env.example first.")
    local = dotenv_values(env_path)
    client_id = os.environ.get("GMAIL_CLIENT_ID") or local.get("GMAIL_CLIENT_ID")
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET") or local.get("GMAIL_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise SystemExit("Set GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET in the local .env before running this helper.")

    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        },
        SCOPES,
    )
    credentials = flow.run_local_server(
        host="localhost",
        port=0,
        open_browser=True,
        access_type="offline",
        prompt="consent",
        include_granted_scopes="true",
    )
    if not credentials.refresh_token:
        raise SystemExit("Google returned no refresh token. Revoke the app grant and rerun with consent prompted.")
    _save_refresh_token(env_path, credentials.refresh_token)
    print(f"GMAIL_REFRESH_TOKEN saved to {env_path}; the token itself was not printed.")
    print("Keep this file untracked. For hosted use, copy the value directly into the platform's encrypted environment settings.")


if __name__ == "__main__":
    main()
