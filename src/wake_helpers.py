"""Small stateful helpers for the wake watcher.

Extracted from wake_watcher.py so that module stays under the 200-line cap.
Each helper is deliberately minimal and clock-injectable for deterministic
unit tests.
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
