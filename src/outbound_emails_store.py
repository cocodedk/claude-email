"""Outbound SMTP Message-ID store — extracted from chat_db.py.

Every reply we send (relay, ACK, JSON envelope, CLI-fallback) records
here so a user reply passes ``security.is_authorized`` via the
chat-thread match without an ``AUTH:`` keyword. Lives in its own
mixin so chat_db.py stays under the 200-line cap as task_id and any
future per-outbound metadata land on this table.
"""
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class OutboundEmailsMixin:
    """Persist + look up SMTP Message-IDs we have sent."""

    def record_outbound_email(
        self, email_message_id: str, *, kind: str, sender_agent: str = "",
    ) -> None:
        if not email_message_id:
            raise ValueError("email_message_id must not be empty")
        self._conn.execute(
            "INSERT INTO outbound_emails "
            "(email_message_id, sent_at, kind, sender_agent) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(email_message_id) DO NOTHING",
            (email_message_id, _now(), kind, sender_agent or None),
        )
        self._conn.commit()

    def find_outbound_email(self, email_message_id: str) -> dict | None:
        if not email_message_id:
            return None
        row = self._conn.execute(
            "SELECT * FROM outbound_emails WHERE email_message_id=?",
            (email_message_id,),
        ).fetchone()
        return dict(row) if row else None
