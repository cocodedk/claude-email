"""_impl_* method bodies for ChatDB messages + events.

Extracted to keep src/chat_db.py under the 200-line cap.
All methods here are inner transaction / read bodies — they never call
.commit() or .rollback(), and they never call a public wrapper.
"""
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChatDbImplsMixin:
    """Inner-body implementations for ChatDB message + event operations."""

    # ── Message _impl bodies ─────────────────────────────

    def _impl_insert_message(
        self, from_name, to_name, body, msg_type, in_reply_to,
        content_type, task_id,
    ) -> dict:
        now = _now()
        cur = self._conn.execute(
            """INSERT INTO messages (from_name, to_name, body, type, status,
                                     in_reply_to, created_at, content_type, task_id)
               VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?)""",
            (from_name, to_name, body, msg_type, in_reply_to, now,
             content_type or None, task_id),
        )
        self._impl_log_event(
            from_name, "message", f"{msg_type} from {from_name} to {to_name}",
        )
        row = self._conn.execute(
            "SELECT * FROM messages WHERE id=?", (cur.lastrowid,)
        ).fetchone()
        self._after_commit.append(self._nudge_wake)
        return dict(row)

    def _impl_get_pending_messages_for(self, to_name: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM messages WHERE to_name=? AND status='pending' ORDER BY id",
            (to_name,),
        ).fetchall()
        return [dict(r) for r in rows]

    def _impl_claim_pending_messages_for(self, to_name: str) -> list[dict]:
        rows = self._conn.execute(
            """UPDATE messages SET status='delivered'
               WHERE id IN (
                   SELECT id FROM messages
                   WHERE to_name=? AND status='pending' ORDER BY id
               )
               RETURNING *""",
            (to_name,),
        ).fetchall()
        return sorted((dict(r) for r in rows), key=lambda r: r["id"])

    def _impl_get_distinct_pending_recipients(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT to_name FROM messages "
            "WHERE status='pending' AND to_name IS NOT NULL"
        ).fetchall()
        return [r["to_name"] for r in rows]

    def _impl_mark_message_delivered(self, msg_id: int) -> None:
        self._conn.execute(
            "UPDATE messages SET status='delivered' WHERE id=?", (msg_id,)
        )

    def _impl_mark_message_failed(self, msg_id: int) -> None:
        self._conn.execute(
            "UPDATE messages SET status='failed' WHERE id=?", (msg_id,)
        )

    def _impl_recover_failed_messages_for(self, to_name: str) -> int:
        cur = self._conn.execute(
            "UPDATE messages SET status='pending' "
            "WHERE to_name=? AND status='failed'",
            (to_name,),
        )
        return cur.rowcount

    def _impl_set_email_message_id(
        self, msg_id: int, email_message_id: str,
    ) -> None:
        self._conn.execute(
            "UPDATE messages SET email_message_id=? WHERE id=?",
            (email_message_id, msg_id),
        )

    def _impl_find_message_by_email_id(
        self, email_message_id: str,
    ) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM messages WHERE email_message_id=?",
            (email_message_id,),
        ).fetchone()
        return dict(row) if row else None

    def _impl_get_message(self, msg_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM messages WHERE id=?", (msg_id,),
        ).fetchone()
        return dict(row) if row else None

    def _impl_get_last_email_message_id_for_agent(
        self, agent_name: str,
    ) -> str | None:
        row = self._conn.execute(
            "SELECT email_message_id FROM messages "
            "WHERE from_name=? AND email_message_id IS NOT NULL "
            "ORDER BY id DESC LIMIT 1",
            (agent_name,),
        ).fetchone()
        return row["email_message_id"] if row else None

    def _impl_get_reply_to_message(self, msg_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM messages WHERE in_reply_to=? "
            "AND type='reply' ORDER BY id DESC LIMIT 1",
            (msg_id,),
        ).fetchone()
        return dict(row) if row else None

    # ── Event _impl body ─────────────────────────────────

    def _impl_log_event(
        self, participant: str, event_type: str, summary: str,
    ) -> None:
        self._conn.execute(
            "INSERT INTO events (event_type, participant, summary, created_at) "
            "VALUES (?, ?, ?, ?)",
            (event_type, participant, summary, _now()),
        )
