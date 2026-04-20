"""Agent registry — mixin for ChatDB.

Split from chat_db.py to keep that module under the 200-line cap.
Methods operate on the connection (`self._conn`) owned by the host class.
"""
from datetime import datetime, timezone

from src.chat_errors import AgentNameTaken, AgentProjectTaken
from src.process_liveness import is_alive


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentRegistryMixin:
    """Agent lifecycle methods (register, reap, status) for ChatDB."""

    def register_agent(
        self, name: str, project_path: str, pid: int | None = None,
    ) -> dict:
        """Register or take over an agent slot.

        When pid is provided, enforces at-most-one-live-owner per name AND
        per project_path: if another live process holds either slot, raise
        AgentNameTaken / AgentProjectTaken. Stale (dead-pid) rows are
        transparently taken over.
        """
        now = _now()
        if pid is not None:
            existing = self.get_agent(name)
            if existing and existing["pid"] is not None and existing["pid"] != pid \
                    and is_alive(existing["pid"]):
                raise AgentNameTaken(name, existing["pid"])
            conflict = self._conn.execute(
                "SELECT name, pid FROM agents "
                "WHERE project_path=? AND name!=? AND pid IS NOT NULL",
                (project_path, name),
            ).fetchone()
            if conflict and is_alive(conflict["pid"]):
                raise AgentProjectTaken(project_path, conflict["name"], conflict["pid"])
        self._conn.execute(
            """INSERT INTO agents (name, project_path, status, pid, registered_at, last_seen_at)
               VALUES (?, ?, 'running', ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                 project_path=excluded.project_path,
                 status='running',
                 pid=COALESCE(excluded.pid, agents.pid),
                 last_seen_at=excluded.last_seen_at""",
            (name, project_path, pid, now, now),
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
