"""Tests for wake_watcher helpers and main loop."""
import asyncio
import os
import tempfile

import pytest

from src.chat_db import ChatDB
from src.wake_spawn import WakeTurnResult
from src.wake_watcher import (
    WakeWatcherConfig,
    _AgentLocks,
    _FailureTracker,
    _SessionCache,
    process_agent,
    run_wake_watcher,
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


def _cfg(**over):
    base = dict(
        interval_secs=0.05, timeout_secs=5,
        idle_expiry_secs=900, max_failures=3, rate_limit_secs=3600,
        claude_bin="claude", prompt="drain", user_avatar="user",
    )
    base.update(over)
    return WakeWatcherConfig(**base)


@pytest.mark.asyncio
async def test_run_wake_watcher_processes_pending_and_stops(live_db, tmp_path):
    live_db.register_agent("agent-foo", str(tmp_path))
    live_db.insert_message("bar", "agent-foo", "hi", "notify")

    seen: list[str] = []

    async def fake_spawn(cmd, cwd, timeout):
        seen.append(cwd)
        for m in live_db.get_pending_messages_for("agent-foo"):
            live_db.mark_message_delivered(m["id"])
        return WakeTurnResult(exit_code=0, timed_out=False)

    stop = asyncio.Event()
    task = asyncio.create_task(
        run_wake_watcher(live_db, _cfg(), stop, spawn_fn=fake_spawn),
    )
    await asyncio.sleep(0.25)
    stop.set()
    await asyncio.wait_for(task, timeout=2)
    assert seen == [str(tmp_path)]


@pytest.mark.asyncio
async def test_run_wake_watcher_swallows_recipient_query_failure(live_db, caplog):
    """A transient DB error on the recipient query must be logged and the loop
    must continue. Previously this path was accidentally exercised by a
    cross-thread sqlite error from the TestClient fixture; with
    check_same_thread=False that incidental coverage went away."""
    import logging
    call_count = {"n": 0}
    original = live_db.get_distinct_pending_recipients

    def flaky():
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated db blip")
        return original()

    live_db.get_distinct_pending_recipients = flaky
    stop = asyncio.Event()

    async def never_spawn(cmd, cwd, timeout):
        raise AssertionError("no pending recipients after recovery")

    with caplog.at_level(logging.ERROR):
        task = asyncio.create_task(
            run_wake_watcher(live_db, _cfg(), stop, spawn_fn=never_spawn),
        )
        await asyncio.sleep(0.2)
        stop.set()
        await asyncio.wait_for(task, timeout=2)
    assert call_count["n"] >= 2  # raised once, then recovered
    assert any("recipient query failed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_run_wake_watcher_shuts_down_cleanly(live_db):
    stop = asyncio.Event()

    async def never_called_spawn(cmd, cwd, timeout):
        raise AssertionError("no pending recipients — should not be called")

    task = asyncio.create_task(
        run_wake_watcher(live_db, _cfg(), stop, spawn_fn=never_called_spawn),
    )
    await asyncio.sleep(0.15)
    stop.set()
    await asyncio.wait_for(task, timeout=2)


@pytest.mark.asyncio
async def test_run_wake_watcher_wakes_on_nudge_before_interval(live_db, tmp_path):
    """A writer that sets nudge must wake the loop well before the poll tick."""
    live_db.register_agent("agent-foo", str(tmp_path))
    nudge = asyncio.Event()
    seen: list[str] = []

    async def fake_spawn(cmd, cwd, timeout):
        seen.append(cwd)
        for m in live_db.get_pending_messages_for("agent-foo"):
            live_db.mark_message_delivered(m["id"])
        return WakeTurnResult(exit_code=0, timed_out=False)

    stop = asyncio.Event()
    # 5s interval — without nudge the test would hang far past its timeout.
    cfg = _cfg(interval_secs=5.0)
    task = asyncio.create_task(
        run_wake_watcher(live_db, cfg, stop, spawn_fn=fake_spawn, nudge=nudge),
    )
    await asyncio.sleep(0.05)  # let iter 1 scan (empty) and enter wait
    live_db.insert_message("bar", "agent-foo", "hi", "notify")
    nudge.set()
    for _ in range(50):  # up to ~1s
        if seen:
            break
        await asyncio.sleep(0.02)
    assert seen == [str(tmp_path)]
    stop.set()
    nudge.set()  # wake the loop so it can observe stop and exit
    await asyncio.wait_for(task, timeout=2)


@pytest.mark.asyncio
async def test_run_wake_watcher_nudge_auto_cleared_between_ticks(live_db, tmp_path):
    """After a nudge wakes the loop, the event clears so the next sleep is
    full-duration unless another write nudges again."""
    live_db.register_agent("agent-foo", str(tmp_path))
    nudge = asyncio.Event()
    spawns = 0

    async def fake_spawn(cmd, cwd, timeout):
        nonlocal spawns
        spawns += 1
        for m in live_db.get_pending_messages_for("agent-foo"):
            live_db.mark_message_delivered(m["id"])
        return WakeTurnResult(exit_code=0, timed_out=False)

    stop = asyncio.Event()
    cfg = _cfg(interval_secs=5.0)
    task = asyncio.create_task(
        run_wake_watcher(live_db, cfg, stop, spawn_fn=fake_spawn, nudge=nudge),
    )
    await asyncio.sleep(0.05)
    live_db.insert_message("bar", "agent-foo", "hi", "notify")
    nudge.set()
    # Wait for the first spawn to complete
    for _ in range(50):
        if spawns >= 1:
            break
        await asyncio.sleep(0.02)
    # No further writes — nudge must be cleared, loop must be sleeping again
    await asyncio.sleep(0.2)
    assert spawns == 1, "nudge stayed set and caused spin"
    stop.set()
    nudge.set()
    await asyncio.wait_for(task, timeout=2)


@pytest.mark.asyncio
async def test_run_wake_watcher_skips_user_avatar(live_db, tmp_path):
    """Messages to the user avatar belong to the email relay, not the watcher."""
    live_db.register_agent("agent-foo", str(tmp_path))
    live_db.insert_message("agent-foo", "user", "outbound", "notify")

    async def fake_spawn(cmd, cwd, timeout):
        raise AssertionError("user-avatar rows must not trigger spawn")

    stop = asyncio.Event()
    task = asyncio.create_task(
        run_wake_watcher(live_db, _cfg(), stop, spawn_fn=fake_spawn),
    )
    await asyncio.sleep(0.15)
    stop.set()
    await asyncio.wait_for(task, timeout=2)


@pytest.mark.asyncio
async def test_process_agent_stalled_spawn_records_failure(live_db, tmp_path):
    """Spawn exits 0 but drains nothing (e.g. project missing drain hook) —
    must count as a failure so escalation eventually fires. Without this, the
    watcher respawns the same stuck agent forever at ~18s per tick."""
    live_db.register_agent("agent-foo", str(tmp_path))
    live_db.insert_message("bar", "agent-foo", "hi", "notify")
    locks = _AgentLocks()
    cache = _SessionCache(idle_secs=900)
    tracker = _FailureTracker(max_failures=3, rate_limit_secs=3600)

    async def stalled_spawn(cmd, cwd, timeout):
        # exit 0 but mark nothing delivered — simulates missing drain hook
        return WakeTurnResult(exit_code=0, timed_out=False)

    await process_agent(
        "agent-foo", live_db, locks, cache, tracker,
        spawn_fn=stalled_spawn, claude_bin="claude", prompt="drain",
        timeout=300, user_avatar="user",
    )
    assert tracker.count("agent-foo") == 1
    # session still cached so a later fix + resume stays prompt-cache warm
    assert cache.get("agent-foo") is not None


@pytest.mark.asyncio
async def test_process_agent_partial_drain_counts_as_success(live_db, tmp_path):
    """Agent that drains 1 of N pending messages is making progress — must
    reset the failure counter, not escalate."""
    live_db.register_agent("agent-foo", str(tmp_path))
    live_db.insert_message("bar", "agent-foo", "m1", "notify")
    live_db.insert_message("bar", "agent-foo", "m2", "notify")
    locks = _AgentLocks()
    cache = _SessionCache(idle_secs=900)
    tracker = _FailureTracker(max_failures=3, rate_limit_secs=3600)
    tracker.record_failure("agent-foo")  # pre-seed so we can see reset

    async def partial_spawn(cmd, cwd, timeout):
        pending = live_db.get_pending_messages_for("agent-foo")
        live_db.mark_message_delivered(pending[0]["id"])  # drain just one
        return WakeTurnResult(exit_code=0, timed_out=False)

    await process_agent(
        "agent-foo", live_db, locks, cache, tracker,
        spawn_fn=partial_spawn, claude_bin="claude", prompt="drain",
        timeout=300, user_avatar="user",
    )
    assert tracker.count("agent-foo") == 0


@pytest.mark.asyncio
async def test_process_agent_expired_persisted_session_is_discarded(
    live_db, tmp_path,
):
    """A persisted wake_session older than cache.idle_secs must not resume —
    next turn builds a fresh session_id and the stale row is deleted."""
    live_db.register_agent("agent-foo", str(tmp_path))
    # Write a persisted row whose last_turn_at is two hours old.
    live_db._conn.execute(
        "INSERT INTO wake_sessions (agent_name, session_id, last_turn_at) "
        "VALUES ('agent-foo', 'stale-uuid', '2026-01-01T00:00:00+00:00')",
    )
    live_db._conn.commit()
    live_db.insert_message("bar", "agent-foo", "hi", "notify")
    locks = _AgentLocks()
    cache = _SessionCache(idle_secs=60)  # very short window
    tracker = _FailureTracker(max_failures=3, rate_limit_secs=3600)
    seen_cmds: list[list[str]] = []

    async def fake_spawn(cmd, cwd, timeout):
        seen_cmds.append(cmd)
        for m in live_db.get_pending_messages_for("agent-foo"):
            live_db.mark_message_delivered(m["id"])
        return WakeTurnResult(exit_code=0, timed_out=False)

    await process_agent(
        "agent-foo", live_db, locks, cache, tracker,
        spawn_fn=fake_spawn, claude_bin="claude", prompt="drain",
        timeout=300, user_avatar="user",
    )
    # Fresh session-id (not stale-uuid), --session-id rather than --resume.
    assert "--session-id" in seen_cmds[0]
    assert "stale-uuid" not in seen_cmds[0]
    # Expired persisted row deleted before the new upsert overwrote it.
    # (The new upsert will have written a NEW session_id; the point is the
    # spawn didn't reuse the stale one.)
    row = live_db.get_wake_session("agent-foo")
    assert row["session_id"] != "stale-uuid"


@pytest.mark.asyncio
async def test_run_wake_watcher_logs_gathered_exception(
    live_db, tmp_path, caplog,
):
    """process_agent raising must surface as a logged error — never silently
    discarded by asyncio.gather(return_exceptions=True)."""
    import logging as _logging
    live_db.register_agent("agent-foo", str(tmp_path))
    live_db.insert_message("bar", "agent-foo", "hi", "notify")

    async def exploding_spawn(cmd, cwd, timeout):
        raise RuntimeError("synthetic spawn crash")

    stop = asyncio.Event()
    with caplog.at_level(_logging.ERROR):
        task = asyncio.create_task(
            run_wake_watcher(live_db, _cfg(), stop, spawn_fn=exploding_spawn),
        )
        await asyncio.sleep(0.2)
        stop.set()
        await asyncio.wait_for(task, timeout=2)
    assert any(
        "process_agent failed for agent-foo" in r.message
        for r in caplog.records
    ), f"missing gather-exception log; got: {[r.message for r in caplog.records]}"


@pytest.mark.asyncio
async def test_process_agent_empty_pending_skips_spawn(live_db, tmp_path):
    """If another consumer (MCP chat_check_messages, concurrent drain) empties
    the queue between recipient scan and process_agent entry, don't spawn —
    avoids counting an inevitable no-progress turn as a failure."""
    live_db.register_agent("agent-foo", str(tmp_path))
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
