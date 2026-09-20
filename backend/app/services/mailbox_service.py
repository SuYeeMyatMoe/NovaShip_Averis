"""
Mailbox polling shared by the `Fetch Inbox` button, the background poller and tests.

A poll pulls messages from one connector, ingests each unseen message through the
normal audited pipeline and, for a user's connected Gmail, stamps the email with
`mailbox_user_id` / `mailbox_address` so the shared desk can show where it came from.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from app.connectors.email_connectors import BaseConnector, GmailConnector, get_connector
from app.contracts.schemas import ActorType, UserMailbox
from app.services.case_service import CaseService


class MailboxPollError(RuntimeError):
    def __init__(self, connector_name: str, cause: Exception) -> None:
        super().__init__(f"connector {connector_name} failed: {type(cause).__name__}")
        self.connector_name = connector_name
        self.cause = cause


def poll_connector(service: CaseService, conn: BaseConnector, *, actor_id: str, limit: int = 25,
                   mailbox: Optional[UserMailbox] = None) -> dict[str, Any]:
    """Ingest up to `limit` messages from `conn`. Idempotent: known message ids and duplicate content are skipped."""
    created: list[str] = []
    skipped = 0
    try:
        for msg in conn.fetch(limit=limit):
            incoming_id = msg.raw.get("email_id") or msg.raw.get("id")
            if incoming_id and service.repo.get_email(str(incoming_id)):
                skipped += 1
                continue
            if mailbox is not None:
                msg.raw["mailbox_user_id"] = mailbox.user_id
                msg.raw["mailbox_address"] = mailbox.address
            email = service.pipe.ingest_email(msg.raw, msg.blobs, provider=msg.provider, received_at=msg.received_at)
            if email.is_duplicate_of:
                skipped += 1
                continue
            case = service.pipe.run(email, actor_id=actor_id)
            created.append(case.id)
    except Exception as exc:  # connector failure is visible + recoverable
        service.pipe.audit(None, ActorType.SYSTEM, "connector", "ERROR",
                           after={"category": "EMAIL_CONNECTOR_ERROR", "message": type(exc).__name__,
                                  **({"mailbox": mailbox.address} if mailbox else {})})
        if mailbox is not None:
            mailbox.status = "error"
            mailbox.last_error = type(exc).__name__
            service.repo.save_mailbox(mailbox)
        raise MailboxPollError(conn.name, exc) from exc
    if mailbox is not None:
        mailbox.last_polled_at = datetime.utcnow()
        mailbox.last_error = None
        if mailbox.status == "error":
            mailbox.status = "active"
        service.repo.save_mailbox(mailbox)
    return {"connector": conn.name, "mailbox": mailbox.address if mailbox else "shared", "created": created, "duplicates_skipped": skipped}


def poll_user_mailbox(service: CaseService, mailbox: UserMailbox, *, actor_id: str, limit: int = 25) -> dict[str, Any]:
    return poll_connector(service, GmailConnector.from_mailbox(mailbox), actor_id=actor_id, limit=limit, mailbox=mailbox)


def poll_shared_mailbox(service: CaseService, *, actor_id: str, limit: int = 25) -> Optional[dict[str, Any]]:
    """Poll the desk mailbox from .env (EMAIL_PROVIDER). Returns None when no shared connector is configured."""
    conn = get_connector()
    if conn is None:
        return None
    return poll_connector(service, conn, actor_id=actor_id, limit=limit)
