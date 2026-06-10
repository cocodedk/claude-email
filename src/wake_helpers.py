"""Small stateful helpers for the wake watcher.

Extracted from wake_watcher.py so that module stays under the 200-line cap.
Each helper is deliberately minimal and clock-injectable for deterministic
unit tests.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone

from src.process_liveness import is_alive
from src.status_envelope import emit_stalled_for_project

logger = logging.getLogger(__name__)


def _has_live_owner(agent: dict) -> bool:
    """True iff a long-lived Claude session owns this agent row.

    When present, that session's own Stop / UserPromptSubmit hooks drain
    the inbox; a transient wake-spawn would race and answer from divergent
    context. Idle-owner messages sit pending until its next turn;
    live-but-stuck is an alert, not a failover."""
    pid = agent.get("pid")
    return bool(pid) and is_alive(pid)


def _is_session_fresh(persisted: dict, idle_secs: float) -> bool:
    """True iff persisted wake-session row is still within idle_expiry_secs.

    Parses last_turn_at (ISO 8601 UTC). Malformed or missing timestamps are
    treated as expired so we don't resume a broken session.
    """
    raw = persisted.get("last_turn_at")
    if not raw:
        return False
    try:
        last = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - last).total_seconds()
    return age <= idle_secs


def tick_due_names(db, *, exclude: set[str], tracker: "_FailureTracker") -> list[str]:
    """Names of tick-configured agents whose periodic wake is due.

    Skips agents already being processed this round (`exclude`) and agents
    in failure escalation — the tick must not become a respawn loop.
    An agent with no wake_session row (or a malformed timestamp) is due.
    """
    due = []
    for row in db.get_tick_candidates():
        name = row["name"]
        if name in exclude or tracker.should_escalate(name):
            continue
        if _is_session_fresh(
            {"last_turn_at": row.get("last_turn_at")}, float(row["tick_secs"]),
        ):
            continue
        due.append(name)
    return due


def _handle_failure(
    db, tracker: "_FailureTracker", agent_name: str, project_path: str,
    result, user_avatar: str,
) -> None:
    tracker.record_failure(agent_name)
    logger.warning(
        "wake: turn failed for %s (exit=%s timeout=%s error=%s)", agent_name,
        getattr(result, "exit_code", "?"), getattr(result, "timed_out", "?"),
        getattr(result, "error", None),
    )
    emit_stalled_for_project(
        db, project_path, reason=f"wake turn failed ({tracker.count(agent_name)}x)",
    )
    if not tracker.should_escalate(agent_name):
        return
    # Always clear stuck pending messages at escalation so the watcher
    # doesn't respawn the same failing agent forever. Rate limiting gates
    # only the user-facing email, not the queue cleanup.
    pending = db.get_pending_messages_for(agent_name)
    for m in pending:
        db.mark_message_failed(m["id"])
    if not tracker.can_notify(agent_name):
        return
    body = (
        f"[wake-watcher] persistent spawn failure\n"
        f"agent: {agent_name}\n"
        f"project: {project_path}\n"
        f"stuck messages: {len(pending)}\n"
        f"last error: exit={getattr(result, 'exit_code', '?')} "
        f"timeout={getattr(result, 'timed_out', '?')} "
        f"error={getattr(result, 'error', None)}"
    )
    db.insert_message("wake-watcher", user_avatar, body, "notify")
    tracker.mark_notified(agent_name)


class _AgentLocks:
    """Non-blocking per-agent lock map. One turn per agent at a time."""

    def __init__(self) -> None:
        self._held: set[str] = set()

    async def try_acquire(self, name: str) -> bool:
        if name in self._held:
            return False
        self._held.add(name)
        return True

    def release(self, name: str) -> None:
        self._held.discard(name)


class _SessionCache:
    """Maps agent_name → session_id, with idle-expiry TTL.

    clock injected for deterministic tests.
    """

    def __init__(
        self, idle_secs: float, clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.idle_secs = idle_secs
        self._clock = clock
        self._data: dict[str, tuple[str, float]] = {}

    def get(self, name: str) -> str | None:
        entry = self._data.get(name)
        if entry is None:
            return None
        session_id, ts = entry
        if self._clock() - ts > self.idle_secs:
            del self._data[name]
            return None
        return session_id

    def set(self, name: str, session_id: str) -> None:
        self._data[name] = (session_id, self._clock())


class _FailureTracker:
    """Tracks consecutive spawn failures per agent and throttles error emails."""

    def __init__(
        self, max_failures: int, rate_limit_secs: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max = max_failures
        self._rate = rate_limit_secs
        self._clock = clock
        self._counts: dict[str, int] = {}
        self._last_notified: dict[str, float] = {}

    def count(self, name: str) -> int:
        return self._counts.get(name, 0)

    def record_failure(self, name: str) -> None:
        self._counts[name] = self._counts.get(name, 0) + 1

    def record_success(self, name: str) -> None:
        self._counts.pop(name, None)

    def should_escalate(self, name: str) -> bool:
        return self._counts.get(name, 0) >= self._max

    def can_notify(self, name: str) -> bool:
        last = self._last_notified.get(name)
        if last is None:
            return True
        return self._clock() - last >= self._rate

    def mark_notified(self, name: str) -> None:
        self._last_notified[name] = self._clock()
