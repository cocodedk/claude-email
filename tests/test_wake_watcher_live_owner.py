"""Tests for wake_watcher helpers and main loop."""
import pytest

from src.wake_spawn import WakeTurnResult
from src.wake_watcher import (
    _AgentLocks,
    _FailureTracker,
    _SessionCache,
    process_agent,
)
from tests._wake_watcher_helpers import live_db  # noqa: F401


@pytest.mark.asyncio
async def test_process_agent_skips_spawn_when_live_owner(
    live_db, tmp_path, monkeypatch,
):
    """Regression: when a long-lived Claude Code session owns the agent
    (alive pid in the agents row), wake_watcher must NOT spawn a transient
    `claude --print` — otherwise the transient's hook drain would claim
    the messages first and answer with divergent context, starving the
    live session. Messages stay pending so the live owner's own Stop /
    UserPromptSubmit hooks claim them on the next turn."""
    live_db.register_agent("agent-foo", str(tmp_path), pid=4242)
    live_db.insert_message("bar", "agent-foo", "hi", "notify")
    monkeypatch.setattr("src.wake_helpers.is_alive", lambda pid: pid == 4242)

    locks = _AgentLocks()
    cache = _SessionCache(idle_secs=900)
    tracker = _FailureTracker(max_failures=3, rate_limit_secs=3600)
    calls: list[list[str]] = []

    async def fake_spawn(cmd, cwd, timeout):
        calls.append(cmd)
        return WakeTurnResult(exit_code=0, timed_out=False)

    await process_agent(
        "agent-foo", live_db, locks, cache, tracker,
        spawn_fn=fake_spawn, claude_bin="claude", prompt="drain",
        timeout=300, user_avatar="user",
    )
    assert calls == []
    assert tracker.count("agent-foo") == 0
    # Message must still be pending — the live owner will claim it via hooks.
    still_pending = live_db.get_pending_messages_for("agent-foo")
    assert len(still_pending) == 1


@pytest.mark.asyncio
async def test_process_agent_spawns_when_pid_dead(
    live_db, tmp_path, monkeypatch,
):
    """Pid in row but the owning process is gone — classic dormant agent.
    Wake_watcher must spawn to revive it; is_alive returning False is the
    discriminator, not pid presence."""
    live_db.register_agent("agent-foo", str(tmp_path), pid=4242)
    live_db.insert_message("bar", "agent-foo", "hi", "notify")
    monkeypatch.setattr("src.wake_helpers.is_alive", lambda pid: False)

    locks = _AgentLocks()
    cache = _SessionCache(idle_secs=900)
    tracker = _FailureTracker(max_failures=3, rate_limit_secs=3600)
    calls: list[list[str]] = []

    async def fake_spawn(cmd, cwd, timeout):
        calls.append(cmd)
        for m in live_db.get_pending_messages_for("agent-foo"):
            live_db.mark_message_delivered(m["id"])
        return WakeTurnResult(exit_code=0, timed_out=False)

    await process_agent(
        "agent-foo", live_db, locks, cache, tracker,
        spawn_fn=fake_spawn, claude_bin="claude", prompt="drain",
        timeout=300, user_avatar="user",
    )
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_process_agent_spawns_when_pid_null(live_db, tmp_path):
    """Backward compat: rows written before pid tracking have pid=NULL.
    Those agents must still be wakeable; the live-owner skip is gated on
    pid truthy AND is_alive, so None short-circuits to False."""
    live_db.register_agent("agent-foo", str(tmp_path))  # no pid
    live_db.insert_message("bar", "agent-foo", "hi", "notify")

    locks = _AgentLocks()
    cache = _SessionCache(idle_secs=900)
    tracker = _FailureTracker(max_failures=3, rate_limit_secs=3600)
    calls: list[list[str]] = []

    async def fake_spawn(cmd, cwd, timeout):
        calls.append(cmd)
        for m in live_db.get_pending_messages_for("agent-foo"):
            live_db.mark_message_delivered(m["id"])
        return WakeTurnResult(exit_code=0, timed_out=False)

    await process_agent(
        "agent-foo", live_db, locks, cache, tracker,
        spawn_fn=fake_spawn, claude_bin="claude", prompt="drain",
        timeout=300, user_avatar="user",
    )
    assert len(calls) == 1
