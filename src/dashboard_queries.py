"""Dashboard read-only queries — mixin for ChatDB.

Kept separate from chat_db.py to preserve its 200-line headroom and
because these methods are monitoring-only (no writes, no side effects).
"""

FLOW_EVENT_TYPES = (
    "hook_drain_stop",       # Stop hook drained peer messages — lane 01
    "hook_drain_session",    # SessionStart / UserPromptSubmit drain — lane 02
    "wake_spawn_start",      # wake_watcher is about to boot an agent — lane 02
    "wake_spawn_end",        # wake_watcher subprocess finished — lane 02
)


class DashboardQueriesMixin:
    """Read-only projections used by the live dashboard."""

    def get_agents_summary(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT name, project_path, status, pid, last_seen_at, registered_at "
            "FROM agents ORDER BY last_seen_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_messages_summary(self, limit: int = 100) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, from_name, to_name, body, type, status, "
            "in_reply_to, created_at, task_id "
            "FROM messages ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_messages_since(self, last_id: int, limit: int = 200) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, from_name, to_name, body, type, status, "
            "in_reply_to, created_at, task_id "
            "FROM messages WHERE id > ? ORDER BY id ASC LIMIT ?",
            (last_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def latest_message_id(self) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(id), 0) AS mx FROM messages"
        ).fetchone()
        return int(row["mx"])

    def get_flow_events_since(
        self, last_id: int, limit: int = 200,
    ) -> list[dict]:
        placeholders = ",".join("?" * len(FLOW_EVENT_TYPES))
        rows = self._conn.execute(
            f"SELECT id, event_type, participant, summary, created_at "
            f"FROM events WHERE id > ? AND event_type IN ({placeholders}) "
            f"ORDER BY id ASC LIMIT ?",
            (last_id, *FLOW_EVENT_TYPES, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def latest_flow_event_id(self) -> int:
        placeholders = ",".join("?" * len(FLOW_EVENT_TYPES))
        row = self._conn.execute(
            f"SELECT COALESCE(MAX(id), 0) AS mx FROM events "
            f"WHERE event_type IN ({placeholders})",
            FLOW_EVENT_TYPES,
        ).fetchone()
        return int(row["mx"])
