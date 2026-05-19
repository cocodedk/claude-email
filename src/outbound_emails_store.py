"""Outbound SMTP Message-ID store — extracted from chat_db.py.

Every reply we send (relay, ACK, JSON envelope, CLI-fallback) records
here so a user reply passes ``security.is_authorized`` via the
chat-thread match without an ``AUTH:`` keyword. Lives in its own
mixin so chat_db.py stays under the 200-line cap as task_id and any
future per-outbound metadata land on this table. Public methods route
writes through _run_tx and reads through _read.
"""
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class OutboundEmailsMixin:
    """Persist + look up SMTP Message-IDs we have sent."""

    def record_outbound_email(
        self, email_message_id: str, *, kind: str, sender_agent: str = "",
        task_id: int | None = None, body: str = "",
        in_reply_to_eid: str = "",
    ) -> None:
        if not email_message_id:
            raise ValueError("email_message_id must not be empty")
        self._run_tx(
            self._impl_record_outbound_email,
            email_message_id, kind, sender_agent, task_id,
            body, in_reply_to_eid,
        )

    def _impl_record_outbound_email(
        self, email_message_id, kind, sender_agent, task_id,
        body, in_reply_to_eid,
    ) -> None:
        # COALESCE on the conflict path so a later bare-call (no body /
        # parent) cannot wipe the body+parent stamped by the original
        # send. New non-null values still win over an earlier NULL.
        self._conn.execute(
            "INSERT INTO outbound_emails "
            "(email_message_id, sent_at, kind, sender_agent, task_id, "
            " body, in_reply_to_eid) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(email_message_id) DO UPDATE SET "
            "task_id = COALESCE(outbound_emails.task_id, excluded.task_id), "
            "body = COALESCE(outbound_emails.body, excluded.body), "
            "in_reply_to_eid = COALESCE("
            "  outbound_emails.in_reply_to_eid, excluded.in_reply_to_eid)",
            (email_message_id, _now(), kind, sender_agent or None, task_id,
             body or None, in_reply_to_eid or None),
        )

    def find_outbound_email(self, email_message_id: str) -> dict | None:
        return self._read(self._impl_find_outbound_email, email_message_id)

    def _impl_find_outbound_email(self, email_message_id) -> dict | None:
        if not email_message_id:
            return None
        row = self._conn.execute(
            "SELECT * FROM outbound_emails WHERE email_message_id=?",
            (email_message_id,),
        ).fetchone()
        return dict(row) if row else None
