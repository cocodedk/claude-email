"""Tests for wake_watcher helpers and main loop."""
import os
import tempfile

import pytest

from src.chat_db import ChatDB
from src.wake_spawn import WakeTurnResult
from src.wake_watcher import (
    _AgentLocks,
    _FailureTracker,
    _SessionCache,
    process_agent,
)


@pytest.fixture
def live_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = ChatDB(path)
    try:
        yield db
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_agent_locks_acquire_release():
    locks = _AgentLocks()
    assert await locks.try_acquire("agent-foo") is True
    locks.release("agent-foo")


@pytest.mark.asyncio
async def test_agent_locks_rejects_concurrent_same_agent():
    locks = _AgentLocks()
    assert await locks.try_acquire("agent-foo") is True
    assert await locks.try_acquire("agent-foo") is False
    locks.release("agent-foo")
    assert await locks.try_acquire("agent-foo") is True
    locks.release("agent-foo")


@pytest.mark.asyncio
async def test_agent_locks_independent_per_agent():
    locks = _AgentLocks()
    assert await locks.try_acquire("agent-a") is True
    assert await locks.try_acquire("agent-b") is True
    locks.release("agent-a")
    locks.release("agent-b")


def test_session_cache_miss():
    cache = _SessionCache(idle_secs=900, clock=lambda: 1000.0)
    assert cache.get("agent-foo") is None


def test_session_cache_hit_within_ttl():
    cache = _SessionCache(idle_secs=900, clock=lambda: 1000.0)
    cache.set("agent-foo", "uuid-1")
    assert cache.get("agent-foo") == "uuid-1"


def test_session_cache_expired_drops_entry():
    t = [1000.0]
    cache = _SessionCache(idle_secs=60, clock=lambda: t[0])
    cache.set("agent-foo", "uuid-1")
    t[0] = 1000.0 + 61
    assert cache.get("agent-foo") is None
    cache.set("agent-foo", "uuid-2")
    assert cache.get("agent-foo") == "uuid-2"


def test_failure_tracker_starts_at_zero():
    ft = _FailureTracker(max_failures=3, rate_limit_secs=3600, clock=lambda: 0.0)
    assert ft.count("agent-foo") == 0


def test_failure_tracker_increment_and_reset():
    ft = _FailureTracker(max_failures=3, rate_limit_secs=3600, clock=lambda: 0.0)
    ft.record_failure("agent-foo")
    ft.record_failure("agent-foo")
    assert ft.count("agent-foo") == 2
    ft.record_success("agent-foo")
    assert ft.count("agent-foo") == 0


def test_failure_tracker_should_escalate():
    ft = _FailureTracker(max_failures=3, rate_limit_secs=3600, clock=lambda: 0.0)
    assert ft.should_escalate("agent-foo") is False
    ft.record_failure("agent-foo")
    ft.record_failure("agent-foo")
    assert ft.should_escalate("agent-foo") is False
    ft.record_failure("agent-foo")
    assert ft.should_escalate("agent-foo") is True


def test_failure_tracker_rate_limits_notifications():
    t = [0.0]
    ft = _FailureTracker(max_failures=1, rate_limit_secs=60, clock=lambda: t[0])
    ft.record_failure("agent-foo")
    assert ft.can_notify("agent-foo") is True
    ft.mark_notified("agent-foo")
    assert ft.can_notify("agent-foo") is False
    t[0] = 61
    assert ft.can_notify("agent-foo") is True


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
async def test_process_agent_escalates_and_rate_limits(live_db, tmp_path):
    live_db.register_agent("agent-foo", str(tmp_path))
    for i in range(3):
        live_db.insert_message("bar", "agent-foo", f"m{i}", "notify")
    locks = _AgentLocks()
    cache = _SessionCache(idle_secs=900)
    t = [0.0]
    tracker = _FailureTracker(
        max_failures=2, rate_limit_secs=3600, clock=lambda: t[0],
    )

    async def failing_spawn(cmd, cwd, timeout):
        return WakeTurnResult(exit_code=-1, timed_out=False, error="boom")

    # 1st failure — no notification yet (below threshold)
    await process_agent(
        "agent-foo", live_db, locks, cache, tracker,
        spawn_fn=failing_spawn, claude_bin="claude", prompt="drain",
        timeout=300, user_avatar="user",
    )
    assert len(live_db.get_pending_messages_for("user")) == 0

    # 2nd failure — escalates: email inserted, stuck messages marked failed
    await process_agent(
        "agent-foo", live_db, locks, cache, tracker,
        spawn_fn=failing_spawn, claude_bin="claude", prompt="drain",
        timeout=300, user_avatar="user",
    )
    notifications = live_db.get_pending_messages_for("user")
    assert len(notifications) == 1
    assert "agent-foo" in notifications[0]["body"]
    assert live_db.get_pending_messages_for("agent-foo") == []

    # Immediate 3rd failure — rate-limited, no new notification
    live_db.insert_message("bar", "agent-foo", "m4", "notify")
    await process_agent(
        "agent-foo", live_db, locks, cache, tracker,
        spawn_fn=failing_spawn, claude_bin="claude", prompt="drain",
        timeout=300, user_avatar="user",
    )
    assert len(live_db.get_pending_messages_for("user")) == 1

    # After rate window elapses, a new failure re-notifies
    t[0] = 3601
    live_db.insert_message("bar", "agent-foo", "m5", "notify")
    await process_agent(
        "agent-foo", live_db, locks, cache, tracker,
        spawn_fn=failing_spawn, claude_bin="claude", prompt="drain",
        timeout=300, user_avatar="user",
    )
    assert len(live_db.get_pending_messages_for("user")) == 2


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
