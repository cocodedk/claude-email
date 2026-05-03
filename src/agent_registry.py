"""Agent registry — mixin for ChatDB.

Split from chat_db.py to keep that module under the 200-line cap.
Methods operate on the connection (`self._conn`) owned by the host class.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

from src.chat_errors import AgentNameTaken
from src.process_liveness import is_alive


DEFAULT_AGENT_FRESHNESS_SEC = 300  # 5min heartbeat window — covers SMTP→IMAP poll latency between agent's last MCP touch and routing decision; reap_dead_agents handles true crashes via is_alive(pid)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cutoff(seconds_ago: int) -> str:
    """ISO-8601 UTC timestamp ``seconds_ago`` in the past — lower-bound for ``last_seen_at`` freshness."""
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


class AgentRegistryMixin:
    """Agent lifecycle methods (register, reap, status) for ChatDB."""

    def register_agent(
        self, name: str, project_path: str, pid: int | None = None,
    ) -> dict:
        """Register or take over an agent slot.

        When pid is provided, enforces at-most-one-live-owner per name:
        if another live process holds the name, raise AgentNameTaken.
        Stale (dead-pid) rows are transparently taken over. Multiple live
        agents may share the same project_path — each must have a
        distinct name.

        The liveness check and the upsert run inside a single
        IMMEDIATE transaction so a concurrent register_agent cannot
        squeeze a conflicting row in between our SELECT and INSERT.
        """
        now = _now()
        insert_sql = (
            "INSERT INTO agents (name, project_path, status, pid, "
            "registered_at, last_seen_at) "
            "VALUES (?, ?, 'running', ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET "
            "  project_path=excluded.project_path, "
            "  status='running', "
            "  pid=COALESCE(excluded.pid, agents.pid), "
            "  last_seen_at=excluded.last_seen_at"
        )
        insert_args = (name, project_path, pid, now, now)
        if pid is not None:
            try:
                self._conn.rollback()  # clear any implicit tx
                self._conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError:
                # Already inside a transaction we can't unwind — fall back
                # to the previous non-atomic path. The ON CONFLICT clause
                # still serialises the final write.
                pass
            try:
                existing = self.get_agent(name)
                if (
                    existing
                    and existing["pid"] is not None
                    and existing["pid"] != pid
                    and is_alive(existing["pid"])
                ):
                    raise AgentNameTaken(name, existing["pid"])
                self._conn.execute(insert_sql, insert_args)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        else:
            self._conn.execute(insert_sql, insert_args)
            self._conn.commit()
        self._log_event(name, "register", f"Agent {name} registered")
        return self.get_agent(name)

    def find_live_owner(
        self, name: str, project_path: str, *,
        exclude_pid: int | None = None,
    ) -> dict | None:
        """Return the first live-process owner ({name, pid}) of the name
        or project slot — checked via is_alive. ``exclude_pid`` filters
        out our own session. Keeps ownership probing off ``db._conn``."""
        by_name = self.get_agent(name)
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

    def get_agent(self, name: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM agents WHERE name=?", (name,)
        ).fetchone()
        return dict(row) if row else None

    def list_agents(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM agents").fetchall()
        return [dict(r) for r in rows]

    def find_live_agent_for_project(
        self, project_path: str,
        freshness_sec: int = DEFAULT_AGENT_FRESHNESS_SEC,
    ) -> dict | None:
        """Return the newest-registered live agent for ``project_path``
        (live = status='running' + last_seen_at within ``freshness_sec``;
        tiebreak is ORDER BY registered_at DESC per v1 design)."""
        cutoff = _cutoff(freshness_sec)
        row = self._conn.execute(
            "SELECT * FROM agents WHERE project_path=? "
            "AND status='running' AND last_seen_at >= ? "
            "ORDER BY registered_at DESC LIMIT 1",
            (project_path, cutoff),
        ).fetchone()
        return dict(row) if row else None

    def agent_status_for_project(
        self, project_path: str,
        freshness_sec: int = DEFAULT_AGENT_FRESHNESS_SEC,
    ) -> str:
        """3-state liveness for ``list_projects.agent_status``:
        connected | disconnected | absent."""
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
                self._log_event(
                    row["name"], "disconnect",
                    f"Agent {row['name']} (PID {row['pid']}) no longer running",
                )
                reaped.append(row["name"])
        return reaped

    def touch_agent(self, name: str) -> None:
        self._conn.execute(
            "UPDATE agents SET last_seen_at=? WHERE name=?", (_now(), name)
        )
        self._conn.commit()
