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
async def test_process_agent_success_first_session(live_db, tmp_path):
    live_db.register_agent("agent-foo", str(tmp_path))
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
        spawn_fn=fake_spawn, claude_bin="claude",
        prompt="drain", timeout=300, user_avatar="user",
    )

    assert len(calls) == 1
    assert "--session-id" in calls[0]
    assert tracker.count("agent-foo") == 0
    assert cache.get("agent-foo") is not None


@pytest.mark.asyncio
async def test_process_agent_resume_path(live_db, tmp_path):
    live_db.register_agent("agent-foo", str(tmp_path))
    live_db.upsert_wake_session("agent-foo", "uuid-pre")
    live_db.insert_message("bar", "agent-foo", "hi", "notify")
    locks = _AgentLocks()
    cache = _SessionCache(idle_secs=900)
    tracker = _FailureTracker(max_failures=3, rate_limit_secs=3600)
    calls = []

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
    assert "--resume" in calls[0]
    assert "uuid-pre" in calls[0]


@pytest.mark.asyncio
async def test_process_agent_skips_when_already_locked(live_db, tmp_path):
    live_db.register_agent("agent-foo", str(tmp_path))
    live_db.insert_message("bar", "agent-foo", "hi", "notify")
    locks = _AgentLocks()
    await locks.try_acquire("agent-foo")  # pre-acquire to simulate in-flight turn
    cache = _SessionCache(idle_secs=900)
    tracker = _FailureTracker(max_failures=3, rate_limit_secs=3600)
    called = []

    async def fake_spawn(cmd, cwd, timeout):
        called.append(cmd)
        return WakeTurnResult(exit_code=0, timed_out=False)

    await process_agent(
        "agent-foo", live_db, locks, cache, tracker,
        spawn_fn=fake_spawn, claude_bin="claude", prompt="drain",
        timeout=300, user_avatar="user",
    )
    assert called == []


@pytest.mark.asyncio
async def test_process_agent_skips_unknown_agent(live_db):
    locks = _AgentLocks()
    cache = _SessionCache(idle_secs=900)
    tracker = _FailureTracker(max_failures=3, rate_limit_secs=3600)
    called = []

    async def fake_spawn(cmd, cwd, timeout):
        called.append(cmd)
        return WakeTurnResult(exit_code=0, timed_out=False)

    await process_agent(
        "agent-ghost", live_db, locks, cache, tracker,
        spawn_fn=fake_spawn, claude_bin="claude", prompt="drain",
        timeout=300, user_avatar="user",
    )
    assert called == []


@pytest.mark.asyncio
async def test_process_agent_fails_messages_for_disconnected_agent(live_db, tmp_path):
    """Once an agent is marked disconnected (by reap_dead_agents or manually),
    wake_watcher must not spawn — Claude is gone, the spawn would stall and
    waste 90s before _handle_failure escalates. Pending DMs flip to failed
    immediately; recover_failed_messages_for re-pendings them on re-register.
    """
    live_db.register_agent("agent-disc", str(tmp_path))
    live_db.update_agent_status("agent-disc", "disconnected")
    live_db.insert_message("bar", "agent-disc", "hi", "notify")
    spawns = []

    async def fake_spawn(cmd, cwd, timeout):
        spawns.append(cmd)
        return WakeTurnResult(exit_code=0, timed_out=False)

    locks = _AgentLocks()
    cache = _SessionCache(idle_secs=900)
    tracker = _FailureTracker(max_failures=3, rate_limit_secs=3600)
    await process_agent(
        "agent-disc", live_db, locks, cache, tracker,
        spawn_fn=fake_spawn, claude_bin="claude", prompt="drain",
        timeout=300, user_avatar="user",
    )
    assert spawns == []
    assert live_db.get_pending_messages_for("agent-disc") == []
    failed = live_db._conn.execute(
        "SELECT count(*) FROM messages WHERE to_name='agent-disc' AND status='failed'",
    ).fetchone()[0]
    assert failed == 1
