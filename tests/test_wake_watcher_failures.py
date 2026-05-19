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

    # Immediate 3rd failure — rate-limited, no new notification, but the
    # stuck message must still be cleared so the watcher isn't stuck in
    # a respawn loop until the rate window elapses.
    live_db.insert_message("bar", "agent-foo", "m4", "notify")
    await process_agent(
        "agent-foo", live_db, locks, cache, tracker,
        spawn_fn=failing_spawn, claude_bin="claude", prompt="drain",
        timeout=300, user_avatar="user",
    )
    assert len(live_db.get_pending_messages_for("user")) == 1
    assert live_db.get_pending_messages_for("agent-foo") == []

    # After rate window elapses, a new failure re-notifies
    t[0] = 3601
    live_db.insert_message("bar", "agent-foo", "m5", "notify")
    await process_agent(
        "agent-foo", live_db, locks, cache, tracker,
        spawn_fn=failing_spawn, claude_bin="claude", prompt="drain",
        timeout=300, user_avatar="user",
    )
    assert len(live_db.get_pending_messages_for("user")) == 2
    assert live_db.get_pending_messages_for("agent-foo") == []


@pytest.mark.asyncio
async def test_escalated_messages_recoverable_after_reregister(
    live_db, tmp_path,
):
    """Escalation is no longer permanent loss — re-register reclaims abandoned mail."""
    live_db.register_agent("agent-foo", str(tmp_path))
    for i in range(3):
        live_db.insert_message("bar", "agent-foo", f"lost-{i}", "notify")
    locks = _AgentLocks()
    cache = _SessionCache(idle_secs=900)
    tracker = _FailureTracker(max_failures=1, rate_limit_secs=3600)

    async def failing_spawn(cmd, cwd, timeout):
        return WakeTurnResult(exit_code=-1, timed_out=False, error="boom")

    # One failure trips escalation (max_failures=1).
    await process_agent(
        "agent-foo", live_db, locks, cache, tracker,
        spawn_fn=failing_spawn, claude_bin="claude", prompt="drain",
        timeout=300, user_avatar="user",
    )
    assert live_db.get_pending_messages_for("agent-foo") == []

    # Agent re-registers → previously-failed messages flip to pending
    # and become deliverable on the next drain.
    live_db.register_agent("agent-foo", str(tmp_path))
    pending = live_db.get_pending_messages_for("agent-foo")
    assert {m["body"] for m in pending} == {"lost-0", "lost-1", "lost-2"}
