"""Wake watcher: asyncio task that drives `claude --print` turns for agents
with pending bus messages. Lives inside the claude-chat.service process
alongside the MCP SSE app.

Split into small helper classes so each can be unit-tested in isolation
and the main loop stays readable.
"""
from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable

from src.chat_db import ChatDB
from src.wake_spawn import WakeTurnResult, build_wake_cmd

logger = logging.getLogger(__name__)


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
        self._idle = idle_secs
        self._clock = clock
        self._data: dict[str, tuple[str, float]] = {}

    def get(self, name: str) -> str | None:
        entry = self._data.get(name)
        if entry is None:
            return None
        session_id, ts = entry
        if self._clock() - ts > self._idle:
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


async def process_agent(
    agent_name: str, db: ChatDB, locks: _AgentLocks, cache: _SessionCache,
    tracker: _FailureTracker, *, spawn_fn, claude_bin: str, prompt: str,
    timeout: float, user_avatar: str,
) -> None:
    """Drive one wake turn for one agent. Safe to call from the loop."""
    if not await locks.try_acquire(agent_name):
        return
    try:
        agent = db.get_agent(agent_name)
        if not agent or not agent.get("project_path"):
            logger.warning("wake: unknown/path-less agent %s", agent_name)
            return
        project_path = agent["project_path"]

        cached = cache.get(agent_name)
        if cached is None:
            persisted = db.get_wake_session(agent_name)
            cached = persisted["session_id"] if persisted else None
        is_resume = cached is not None
        session_id = cached or str(uuid.uuid4())

        cmd = build_wake_cmd(claude_bin, session_id, is_resume, prompt)
        result = await spawn_fn(cmd, cwd=project_path, timeout=timeout)

        if isinstance(result, WakeTurnResult) and result.exit_code == 0:
            cache.set(agent_name, session_id)
            db.upsert_wake_session(agent_name, session_id)
            tracker.record_success(agent_name)
        else:
            _handle_failure(
                db, tracker, agent_name, project_path, result, user_avatar,
            )
    finally:
        locks.release(agent_name)


def _handle_failure(
    db: ChatDB, tracker: _FailureTracker, agent_name: str, project_path: str,
    result, user_avatar: str,
) -> None:
    tracker.record_failure(agent_name)
    logger.warning(
        "wake: turn failed for %s (exit=%s timeout=%s error=%s)",
        agent_name,
        getattr(result, "exit_code", "?"),
        getattr(result, "timed_out", "?"),
        getattr(result, "error", None),
    )
    if not tracker.should_escalate(agent_name):
        return
    if not tracker.can_notify(agent_name):
        return
    pending = db.get_pending_messages_for(agent_name)
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
    for m in pending:
        db.mark_message_failed(m["id"])
    tracker.mark_notified(agent_name)
