"""_impl_* method bodies for AgentRegistryMixin.

Extracted to keep src/agent_registry.py under the 200-line cap.
All methods here are inner transaction / read bodies — they never call
.commit() or .rollback(), and they never call a public wrapper.
"""
from datetime import datetime, timedelta, timezone

from src.chat_errors import AgentNameTaken
from src.process_liveness import is_alive


DEFAULT_AGENT_FRESHNESS_SEC = 300


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cutoff(seconds_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


_INSERT_AGENT_SQL = (
    "INSERT INTO agents (name, project_path, status, pid, "
    "registered_at, last_seen_at) "
    "VALUES (?, ?, 'running', ?, ?, ?) "
    "ON CONFLICT(name) DO UPDATE SET "
    "  project_path=excluded.project_path, "
    "  status='running', "
    "  pid=COALESCE(excluded.pid, agents.pid), "
    "  last_seen_at=excluded.last_seen_at"
)


class AgentRegistryImplsMixin:
    """Inner-body implementations for agent lifecycle operations."""

    # ── Agent writer _impl bodies ─────────────────────────

    def _impl_register_agent(self, name, project_path, pid) -> dict:
        now = _now()
        insert_args = (name, project_path, pid, now, now)
        if pid is not None:
            existing = self._impl_get_agent(name)
            if (
                existing
                and existing["pid"] is not None
                and existing["pid"] != pid
                and is_alive(existing["pid"])
            ):
                raise AgentNameTaken(name, existing["pid"])
        self._conn.execute(_INSERT_AGENT_SQL, insert_args)
        self._impl_log_event(name, "register", f"Agent {name} registered")
        recovered = self._impl_recover_failed_messages_for(name)
        if recovered > 0:
            self._impl_log_event(
                name, "messages_recovered",
                f"Recovered {recovered} failed messages on re-register",
            )
        return self._impl_get_agent(name)

    def _impl_update_agent_status(self, name: str, status: str) -> None:
        self._conn.execute(
            "UPDATE agents SET status=? WHERE name=?", (status, name),
        )

    def _impl_update_agent_pid(self, name: str, pid: int) -> None:
        self._conn.execute(
            "UPDATE agents SET pid=? WHERE name=?", (pid, name),
        )

    def _impl_touch_agent(self, name: str) -> None:
        self._conn.execute(
            "UPDATE agents SET last_seen_at=? WHERE name=?", (_now(), name),
        )

    def _impl_disconnect(self, name: str, why: str) -> None:
        self._impl_update_agent_status(name, "disconnected")
        self._impl_log_event(name, "disconnect", f"Agent {name} {why}")

    def _impl_reap_dead_agents(self, no_pid_idle_secs: int) -> list:
        reaped: list = []
        for row in self._conn.execute(
            "SELECT name, pid FROM agents WHERE pid IS NOT NULL AND status='running'"
        ).fetchall():
            if not is_alive(row["pid"]):
                self._impl_disconnect(row["name"], f"PID {row['pid']} no longer running")
                reaped.append(row["name"])
        for row in self._conn.execute(
            "SELECT name FROM agents "
            "WHERE pid IS NULL AND status='running' AND last_seen_at < ?",
            (_cutoff(no_pid_idle_secs),),
        ).fetchall():
            self._impl_disconnect(row["name"], "no PID, idle past freshness window")
            reaped.append(row["name"])
        return reaped

    # ── Agent reader _impl bodies ─────────────────────────

    def _impl_get_agent(self, name: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM agents WHERE name=?", (name,),
        ).fetchone()
        return dict(row) if row else None

    def _impl_list_agents(self) -> list:
        rows = self._conn.execute("SELECT * FROM agents").fetchall()
        return [dict(r) for r in rows]

    def _impl_find_live_owner(
        self, name: str, project_path: str, exclude_pid: int | None,
    ) -> dict | None:
        by_name = self._impl_get_agent(name)
        if (
            by_name
            and by_name["pid"] is not None
            and by_name["pid"] != exclude_pid
            and is_alive(by_name["pid"])
        ):
            return {"name": by_name["name"], "pid": by_name["pid"]}
        rows = self._conn.execute(
            "SELECT name, pid FROM agents "
            "WHERE project_path=? AND name!=? AND pid IS NOT NULL",
            (project_path, name),
        ).fetchall()
        for row in rows:
            if row["pid"] == exclude_pid:
                continue
            if is_alive(row["pid"]):
                return {"name": row["name"], "pid": row["pid"]}
        return None

    def _impl_find_live_agent_for_project(
        self, project_path: str, freshness_sec: int,
    ) -> dict | None:
        cutoff = _cutoff(freshness_sec)
        row = self._conn.execute(
            "SELECT * FROM agents WHERE project_path=? "
            "AND status='running' AND last_seen_at >= ? "
            "ORDER BY registered_at DESC LIMIT 1",
            (project_path, cutoff),
        ).fetchone()
        return dict(row) if row else None

    def _impl_agent_status_for_project(
        self, project_path: str, freshness_sec: int,
    ) -> str:
        rows = self._conn.execute(
            "SELECT status, last_seen_at FROM agents WHERE project_path=?",
            (project_path,),
        ).fetchall()
        if not rows:
            return "absent"
        cutoff = _cutoff(freshness_sec)
        for row in rows:
            if row["status"] == "running" and (row["last_seen_at"] or "") >= cutoff:
                return "connected"
        return "disconnected"
