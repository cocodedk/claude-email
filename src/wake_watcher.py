"""Wake watcher: asyncio task that drives `claude --print` turns for agents
with pending bus messages. Lives inside the claude-chat.service process
alongside the MCP SSE app.

Split into small helper classes so each can be unit-tested in isolation
and the main loop stays readable.
"""
from __future__ import annotations

import time
from collections.abc import Callable


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
