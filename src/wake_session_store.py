"""Wake-session persistence — mixin for ChatDB.

Split from chat_db.py to keep that module under the 200-line cap.
Public methods route writes through _run_tx and reads through _read;
bodies live in the _impl_* methods below.
"""
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WakeSessionStoreMixin:
    """Wake-session methods consumed by the wake_watcher background task."""

    def get_wake_session(self, agent_name: str) -> dict | None:
        return self._read(self._impl_get_wake_session, agent_name)

    def _impl_get_wake_session(self, agent_name: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM wake_sessions WHERE agent_name=?", (agent_name,),
        ).fetchone()
        return dict(row) if row else None

    def upsert_wake_session(self, agent_name: str, session_id: str) -> None:
        self._run_tx(self._impl_upsert_wake_session, agent_name, session_id)

    def _impl_upsert_wake_session(self, agent_name, session_id) -> None:
        self._conn.execute(
            """INSERT INTO wake_sessions (agent_name, session_id, last_turn_at)
               VALUES (?, ?, ?)
               ON CONFLICT(agent_name) DO UPDATE SET
                 session_id=excluded.session_id,
                 last_turn_at=excluded.last_turn_at""",
            (agent_name, session_id, _now()),
        )

    def delete_wake_session(self, agent_name: str) -> None:
        self._run_tx(self._impl_delete_wake_session, agent_name)

    def _impl_delete_wake_session(self, agent_name: str) -> None:
        self._conn.execute(
            "DELETE FROM wake_sessions WHERE agent_name=?", (agent_name,),
        )
