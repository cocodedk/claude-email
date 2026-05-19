"""Agent registry — mixin for ChatDB.

Split from chat_db.py to keep that module under the 200-line cap.
Public methods are thin wrappers that route writes through _run_tx
and reads through _read; bodies live in AgentRegistryImplsMixin.
"""
from src.agent_registry_impls import (
    AgentRegistryImplsMixin,
    DEFAULT_AGENT_FRESHNESS_SEC,
    _cutoff,
    _now,
)

__all__ = ["AgentRegistryMixin", "DEFAULT_AGENT_FRESHNESS_SEC"]


class AgentRegistryMixin(AgentRegistryImplsMixin):
    """Agent lifecycle methods (register, reap, status) for ChatDB."""

    def register_agent(
        self, name: str, project_path: str, pid: int | None = None,
    ) -> dict:
        """Register or take over an agent slot.

        With pid: enforces at-most-one-live-owner per name (raises
        AgentNameTaken on conflict, takes over stale rows). Multiple
        live agents may share project_path — each needs a distinct name.
        Liveness check + upsert run inside _run_tx (IMMEDIATE transaction)
        so a concurrent register_agent can't race SELECT/INSERT.
        """
        return self._run_tx(self._impl_register_agent, name, project_path, pid)

    def find_live_owner(
        self, name: str, project_path: str, *,
        exclude_pid: int | None = None,
    ) -> dict | None:
        """Return the first live-process owner ({name, pid}) of the name
        or project slot. ``exclude_pid`` filters out our own session."""
        return self._read(
            self._impl_find_live_owner, name, project_path, exclude_pid,
        )

    def get_agent(self, name: str) -> dict | None:
        return self._read(self._impl_get_agent, name)

    def list_agents(self) -> list[dict]:
        return self._read(self._impl_list_agents)

    def find_live_agent_for_project(
        self, project_path: str,
        freshness_sec: int = DEFAULT_AGENT_FRESHNESS_SEC,
    ) -> dict | None:
        """Return newest-registered live agent for ``project_path``
        (live = running + last_seen_at within ``freshness_sec``).
        Multiple live agents share a project (post-2026-05-03); callers
        needing disambiguation must use the explicit name."""
        return self._read(
            self._impl_find_live_agent_for_project, project_path, freshness_sec,
        )

    def agent_status_for_project(
        self, project_path: str,
        freshness_sec: int = DEFAULT_AGENT_FRESHNESS_SEC,
    ) -> str:
        """3-state liveness for ``list_projects.agent_status``:
        connected | disconnected | absent."""
        return self._read(
            self._impl_agent_status_for_project, project_path, freshness_sec,
        )

    def update_agent_status(self, name: str, status: str) -> None:
        self._run_tx(self._impl_update_agent_status, name, status)

    def update_agent_pid(self, name: str, pid: int) -> None:
        self._run_tx(self._impl_update_agent_pid, name, pid)

    def touch_agent(self, name: str) -> None:
        self._run_tx(self._impl_touch_agent, name)

    def reap_dead_agents(
        self, no_pid_idle_secs: int = DEFAULT_AGENT_FRESHNESS_SEC,
    ) -> list[str]:
        """Mark dead agents as disconnected; reap zombie children.

        Also sweeps pid=NULL rows idle past ``no_pid_idle_secs`` — MCP-only
        registrations leave pid=NULL and would otherwise linger as 'running'
        ghosts. Live MCP-only sessions touch last_seen_at via outbound tool
        calls, so genuine activity protects the row."""
        return self._run_tx(self._impl_reap_dead_agents, no_pid_idle_secs)

    def _disconnect(self, name: str, why: str) -> None:
        self._run_tx(self._impl_disconnect, name, why)
