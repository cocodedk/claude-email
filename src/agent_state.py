"""3-state liveness vocabulary for envelope v: 2 list_projects responses.

Split from src/agent_registry.py to keep that module under the
200-line cap. Wired into ChatDB via the same mixin pattern the
registry uses. Public methods route reads through _read.

Returns ``online | stale | offline`` per project_path. Per-row precedence:

- pid set + ``is_alive(pid)=True``  → ``online`` (overrides timestamp,
  covers PreCompact gaps and bus-restart pauses).
- pid set + ``is_alive(pid)=False`` → ``offline`` (authoritative death).
- pid NULL + ``last_seen_at`` within freshness window → ``online``.
- pid NULL + ``last_seen_at`` within ghost window  → ``stale``.
- otherwise (no rows, deregistered, beyond ghost) → ``offline``.

Best-of-rows: any ``online`` row wins; else any ``stale`` wins; else
``offline``. Two thresholds reused from existing modules — heartbeat
from ``agent_registry.DEFAULT_AGENT_FRESHNESS_SEC`` (5 min), ghost
from ``dashboard_queries.DEFAULT_AGENT_STALE_SECS`` (30 min) — so the
dashboard's ghost filter and the wire vocab agree by construction.
"""
from src.agent_registry import DEFAULT_AGENT_FRESHNESS_SEC, _cutoff
from src.dashboard_queries import DEFAULT_AGENT_STALE_SECS
from src.process_liveness import is_alive


def _row_state(row, fresh_cutoff: str, stale_cutoff: str) -> str:
    pid = row["pid"]
    status = row["status"]
    seen = row["last_seen_at"] or ""
    if status in ("deregistered", "disconnected"):
        return "offline"
    if pid is not None:
        return "online" if is_alive(pid) else "offline"
    if seen >= fresh_cutoff:
        return "online"
    if seen >= stale_cutoff:
        return "stale"
    return "offline"


class AgentStateMixin:
    """Mixin exposing the new 3-state liveness on ChatDB."""

    def agent_state_for_project(
        self, project_path: str,
        freshness_sec: int = DEFAULT_AGENT_FRESHNESS_SEC,
        stale_sec: int = DEFAULT_AGENT_STALE_SECS,
    ) -> str:
        return self._read(
            self._impl_agent_state_for_project,
            project_path, freshness_sec, stale_sec,
        )

    def _impl_agent_state_for_project(
        self, project_path: str,
        freshness_sec: int,
        stale_sec: int,
    ) -> str:
        rows = self._conn.execute(
            "SELECT pid, status, last_seen_at FROM agents "
            "WHERE project_path=?",
            (project_path,),
        ).fetchall()
        if not rows:
            return "offline"
        fresh_cutoff = _cutoff(freshness_sec)
        stale_cutoff = _cutoff(stale_sec)
        best = "offline"
        for row in rows:
            verdict = _row_state(row, fresh_cutoff, stale_cutoff)
            if verdict == "online":
                return "online"
            if verdict == "stale":
                best = "stale"
        return best
