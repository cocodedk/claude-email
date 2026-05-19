"""Tests for wake_watcher helpers and main loop."""
import asyncio

import pytest

from src.wake_spawn import WakeTurnResult
from src.wake_watcher import (
    _AgentLocks,
    _FailureTracker,
    _SessionCache,
    process_agent,
    run_wake_watcher,
)
from tests._wake_watcher_helpers import _cfg, live_db  # noqa: F401


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


@pytest.mark.asyncio
async def test_process_agent_emits_wake_spawn_flow_events(live_db, tmp_path):
    """Dashboard flow panel depends on wake_spawn_start/end events being
    written to the shared events table so the second-face diagram can
    animate the cold-wake path when it fires."""
    live_db.register_agent("agent-foo", str(tmp_path))
    live_db.insert_message("peer", "agent-foo", "hi", "notify")

    async def fake_spawn(cmd, cwd, timeout):
        for m in live_db.get_pending_messages_for("agent-foo"):
            live_db.mark_message_delivered(m["id"])
        return WakeTurnResult(exit_code=0, timed_out=False)

    await process_agent(
        "agent-foo", live_db, _AgentLocks(), _SessionCache(idle_secs=900),
        _FailureTracker(max_failures=3, rate_limit_secs=3600),
        spawn_fn=fake_spawn, claude_bin="claude", prompt="drain",
        timeout=300, user_avatar="user",
    )
    rows = live_db.get_flow_events_since(0)
    types = [r["event_type"] for r in rows]
    assert types == ["wake_spawn_start", "wake_spawn_end"]
    end = rows[1]
    assert "exit=0" in end["summary"]


@pytest.mark.asyncio
async def test_process_agent_emits_wake_spawn_end_on_failure(live_db, tmp_path):
    """A failed spawn (non-zero exit / timeout) still emits wake_spawn_end
    so the dashboard reflects the attempt, not just successes."""
    live_db.register_agent("agent-foo", str(tmp_path))
    live_db.insert_message("peer", "agent-foo", "hi", "notify")

    async def fake_spawn(cmd, cwd, timeout):
        return WakeTurnResult(exit_code=1, timed_out=True, error="boom")

    await process_agent(
        "agent-foo", live_db, _AgentLocks(), _SessionCache(idle_secs=900),
        _FailureTracker(max_failures=3, rate_limit_secs=3600),
        spawn_fn=fake_spawn, claude_bin="claude", prompt="drain",
        timeout=300, user_avatar="user",
    )
    types = [r["event_type"] for r in live_db.get_flow_events_since(0)]
    # Both start and end fire regardless of exit code.
    assert "wake_spawn_start" in types
    assert "wake_spawn_end" in types
