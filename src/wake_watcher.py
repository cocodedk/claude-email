"""Wake watcher: asyncio task that drives `claude --print` turns for agents
with pending bus messages. Lives inside the claude-chat.service process
alongside the MCP SSE app.

Split into small helper classes so each can be unit-tested in isolation
and the main loop stays readable.
"""
from __future__ import annotations


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
