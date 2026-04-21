"""Wake-session persistence — mixin for ChatDB.

Split from chat_db.py to keep that module under the 200-line cap.
Methods operate on the connection (`self._conn`) owned by the host class.
"""
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WakeSessionStoreMixin:
    """Wake-session methods consumed by the wake_watcher background task."""

    def get_wake_session(self, agent_name: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM wake_sessions WHERE agent_name=?", (agent_name,),
        ).fetchone()
        return dict(row) if row else None

    def upsert_wake_session(self, agent_name: str, session_id: str) -> None:
        self._conn.execute(
            """INSERT INTO wake_sessions (agent_name, session_id, last_turn_at)
               VALUES (?, ?, ?)
               ON CONFLICT(agent_name) DO UPDATE SET
                 session_id=excluded.session_id,
                 last_turn_at=excluded.last_turn_at""",
            (agent_name, session_id, _now()),
        )
        self._conn.commit()

    def delete_wake_session(self, agent_name: str) -> None:
        self._conn.execute(
            "DELETE FROM wake_sessions WHERE agent_name=?", (agent_name,),
        )
        self._conn.commit()
