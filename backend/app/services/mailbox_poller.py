"""
Background poller: every GMAIL_POLL_INTERVAL_SECONDS, pull new mail from every
connected user mailbox and then from the shared desk mailbox (if EMAIL_PROVIDER=gmail).

Off by default (interval 0) so tests, the offline demo and scoring runs are
unaffected. Runs never overlap and never raise into the API process; a failing
mailbox is marked `error` with `last_error` and retried on the next tick.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any

from app.config import get_repo
from app.services.case_service import CaseService
from app.services.mailbox_service import MailboxPollError, poll_shared_mailbox, poll_user_mailbox

log = logging.getLogger("novaship.poller")
SCHEDULER_ACTOR = "scheduler"

_run_lock = threading.Lock()
_stop = threading.Event()
_thread: threading.Thread | None = None


def poll_interval_seconds() -> int:
    try:
        return max(0, int(os.environ.get("GMAIL_POLL_INTERVAL_SECONDS", "0") or 0))
    except ValueError:
        return 0


def poll_all_mailboxes(limit: int = 25) -> dict[str, Any]:
    """One scheduler tick. Returns a per-mailbox summary; never raises."""
    if not _run_lock.acquire(blocking=False):
        return {"skipped": "previous run still in progress"}
    summary: dict[str, Any] = {"mailboxes": {}, "shared": None}
    try:
        service = CaseService(get_repo())
        for mailbox in service.repo.list_mailboxes():
            if mailbox.status == "revoked":
                continue
            try:
                result = poll_user_mailbox(service, mailbox, actor_id=SCHEDULER_ACTOR, limit=limit)
                summary["mailboxes"][mailbox.address] = {"created": len(result["created"]), "duplicates_skipped": result["duplicates_skipped"]}
            except MailboxPollError as exc:
                summary["mailboxes"][mailbox.address] = {"error": type(exc.cause).__name__}
            except Exception as exc:  # token decrypt / config problems must not stop the other mailboxes
                summary["mailboxes"][mailbox.address] = {"error": type(exc).__name__}
                log.warning("mailbox %s skipped: %s", mailbox.address, type(exc).__name__)
        if os.environ.get("EMAIL_PROVIDER", "none").strip().lower() == "gmail":
            try:
                result = poll_shared_mailbox(service, actor_id=SCHEDULER_ACTOR, limit=limit)
                if result:
                    summary["shared"] = {"created": len(result["created"]), "duplicates_skipped": result["duplicates_skipped"]}
            except Exception as exc:
                summary["shared"] = {"error": type(exc).__name__}
    except Exception as exc:  # repository unavailable etc.
        log.warning("poll tick failed: %s", type(exc).__name__)
        summary["error"] = type(exc).__name__
    finally:
        _run_lock.release()
    return summary


def _loop(interval: int) -> None:
    while not _stop.wait(interval):
        result = poll_all_mailboxes()
        log.info("poll tick: %s", result)


def start_background_poller() -> bool:
    """Start the daemon thread when an interval is configured. Returns whether it was started."""
    global _thread
    interval = poll_interval_seconds()
    if interval <= 0 or (_thread and _thread.is_alive()):
        return False
    _stop.clear()
    _thread = threading.Thread(target=_loop, args=(interval,), name="mailbox-poller", daemon=True)
    _thread.start()
    log.info("background mailbox poller started (every %ss)", interval)
    return True


def stop_background_poller() -> None:
    _stop.set()
