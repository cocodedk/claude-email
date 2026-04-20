"""Shared SQLite database layer for the claude-chat system."""
import sqlite3
from datetime import datetime, timedelta, timezone

from src.chat_schema import MIGRATIONS as _MIGRATIONS, SCHEMA as _SCHEMA
from src.process_liveness import is_alive


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChatDB:
    """Single entry-point for all chat DB operations."""

    def __init__(self, path: str):
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        for stmt in _MIGRATIONS:
            try:
                self._conn.execute(stmt)
            except sqlite3.OperationalError:
                pass  # column/index already present
        self._conn.commit()

    # ── Agents ──────────────────────────────────────────────

    def register_agent(self, name: str, project_path: str) -> dict:
        now = _now()
        self._conn.execute(
            """INSERT INTO agents (name, project_path, status, registered_at, last_seen_at)
               VALUES (?, ?, 'running', ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                 project_path=excluded.project_path,
                 status='running',
                 last_seen_at=excluded.last_seen_at""",
            (name, project_path, now, now),
        )
        self._conn.commit()
        self._log_event(name, "register", f"Agent {name} registered")
        return self.get_agent(name)

    def get_agent(self, name: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM agents WHERE name=?", (name,)
        ).fetchone()
        return dict(row) if row else None

    def list_agents(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM agents").fetchall()
        return [dict(r) for r in rows]

    def update_agent_status(self, name: str, status: str) -> None:
        self._conn.execute(
            "UPDATE agents SET status=? WHERE name=?", (status, name)
        )
        self._conn.commit()

    def update_agent_pid(self, name: str, pid: int) -> None:
        self._conn.execute(
            "UPDATE agents SET pid=? WHERE name=?", (pid, name)
        )
        self._conn.commit()

    def reap_dead_agents(self) -> list[str]:
        """Mark dead agents as disconnected; reap zombie children via is_alive."""
        rows = self._conn.execute(
            "SELECT name, pid FROM agents WHERE pid IS NOT NULL AND status='running'"
        ).fetchall()
        reaped = []
        for row in rows:
            if not is_alive(row["pid"]):
                self.update_agent_status(row["name"], "disconnected")
                self._log_event(row["name"], "disconnect", f"Agent {row['name']} (PID {row['pid']}) no longer running")
                reaped.append(row["name"])
        return reaped

    def touch_agent(self, name: str) -> None:
        self._conn.execute(
            "UPDATE agents SET last_seen_at=? WHERE name=?", (_now(), name)
        )
        self._conn.commit()

    # ── Messages ────────────────────────────────────────────

    def insert_message(
        self, from_name: str, to_name: str, body: str,
        msg_type: str, in_reply_to: int | None = None,
        content_type: str = "", task_id: int | None = None,
    ) -> dict:
        now = _now()
        cur = self._conn.execute(
            """INSERT INTO messages (from_name, to_name, body, type, status,
                                     in_reply_to, created_at, content_type, task_id)
               VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?)""",
            (from_name, to_name, body, msg_type, in_reply_to, now,
             content_type or None, task_id),
        )
        self._conn.commit()
        self._log_event(from_name, "message", f"{msg_type} from {from_name} to {to_name}")
        row = self._conn.execute(
            "SELECT * FROM messages WHERE id=?", (cur.lastrowid,)
        ).fetchone()
        return dict(row)

    def get_pending_messages_for(self, to_name: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM messages WHERE to_name=? AND status='pending' ORDER BY id",
            (to_name,),
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_message_delivered(self, msg_id: int) -> None:
        self._conn.execute(
            "UPDATE messages SET status='delivered' WHERE id=?", (msg_id,)
        )
        self._conn.commit()

    def mark_message_failed(self, msg_id: int) -> None:
        self._conn.execute("UPDATE messages SET status='failed' WHERE id=?", (msg_id,))
        self._conn.commit()

    def set_email_message_id(self, msg_id: int, email_message_id: str) -> None:
        self._conn.execute(
            "UPDATE messages SET email_message_id=? WHERE id=?",
            (email_message_id, msg_id),
        )
        self._conn.commit()

    def find_message_by_email_id(self, email_message_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM messages WHERE email_message_id=?",
            (email_message_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_message(self, msg_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM messages WHERE id=?", (msg_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_last_email_message_id_for_agent(self, agent_name: str) -> str | None:
        row = self._conn.execute(
            "SELECT email_message_id FROM messages "
            "WHERE from_name=? AND email_message_id IS NOT NULL "
            "ORDER BY id DESC LIMIT 1",
            (agent_name,),
        ).fetchone()
        return row["email_message_id"] if row else None

    def get_reply_to_message(self, msg_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM messages WHERE in_reply_to=? AND type='reply' ORDER BY id DESC LIMIT 1",
            (msg_id,),
        ).fetchone()
        return dict(row) if row else None

    def cleanup_old(self, days: int = 30) -> dict:
        """Prune delivered/failed messages and old events. Pending rows preserved."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        m = self._conn.execute(
            "DELETE FROM messages WHERE status IN ('delivered','failed') AND created_at < ?",
            (cutoff,),
        ).rowcount
        e = self._conn.execute(
            "DELETE FROM events WHERE created_at < ?", (cutoff,)
        ).rowcount
        self._conn.commit()
        return {"messages": m, "events": e}

    # ── Wake sessions ──────────────────────────────────────

    def get_wake_session(self, agent_name: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM wake_sessions WHERE agent_name=?", (agent_name,),
        ).fetchone()
        return dict(row) if row else None

    # ── Events (internal) ──────────────────────────────────

    def _log_event(self, participant: str, event_type: str, summary: str) -> None:
        self._conn.execute(
            "INSERT INTO events (event_type, participant, summary, created_at) VALUES (?, ?, ?, ?)",
            (event_type, participant, summary, _now()),
        )
        self._conn.commit()
