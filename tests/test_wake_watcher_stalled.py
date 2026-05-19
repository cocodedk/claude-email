"""Tests for wake_watcher helpers and main loop."""
import pytest

from src.task_queue import TaskQueue
from src.wake_spawn import WakeTurnResult
from src.wake_watcher import (
    _AgentLocks,
    _FailureTracker,
    _SessionCache,
    process_agent,
)
from tests._wake_watcher_helpers import live_db  # noqa: F401


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
async def test_stalled_wake_emits_status_for_running_task(live_db, tmp_path):
    """When an agent's wake spawn stalls and it has a task running in its
    project, emit a kind=status envelope (data.status=stalled) so the
    client can light up a 'stuck' glyph. Deduped via last_sent_status."""
    import json
    live_db.register_agent("agent-foo", str(tmp_path))
    live_db.insert_message("bar", "agent-foo", "hi", "notify")
    tq = TaskQueue(live_db.path)
    task_id = tq.enqueue(str(tmp_path), "work", origin_content_type="application/json")
    tq.claim_next(str(tmp_path))
    locks = _AgentLocks()
    cache = _SessionCache(idle_secs=900)
    tracker = _FailureTracker(max_failures=3, rate_limit_secs=3600)

    async def stalled_spawn(cmd, cwd, timeout):
        return WakeTurnResult(exit_code=0, timed_out=False)

    await process_agent(
        "agent-foo", live_db, locks, cache, tracker,
        spawn_fn=stalled_spawn, claude_bin="claude", prompt="drain",
        timeout=300, user_avatar="user",
    )
    rows = live_db._conn.execute(
        "SELECT body FROM messages WHERE content_type='application/json' "
        "AND task_id=? ORDER BY id", (task_id,),
    ).fetchall()
    assert len(rows) == 1
    env = json.loads(rows[0]["body"])
    assert env["kind"] == "status"
    assert env["data"]["status"] == "stalled"
    assert "reason" in env["data"]

    # Second stall must NOT re-emit — dedup via last_sent_status.
    await process_agent(
        "agent-foo", live_db, locks, cache, tracker,
        spawn_fn=stalled_spawn, claude_bin="claude", prompt="drain",
        timeout=300, user_avatar="user",
    )
    rows2 = live_db._conn.execute(
        "SELECT body FROM messages WHERE content_type='application/json' "
        "AND task_id=?", (task_id,),
    ).fetchall()
    assert len(rows2) == 1


@pytest.mark.asyncio
async def test_stalled_wake_without_running_task_no_status(live_db, tmp_path):
    """No running task = no status envelope. Agent-level stall is still
    tracked, but there's nothing task-linked to surface to the client."""
    live_db.register_agent("agent-foo", str(tmp_path))
    live_db.insert_message("bar", "agent-foo", "hi", "notify")
    locks = _AgentLocks()
    cache = _SessionCache(idle_secs=900)
    tracker = _FailureTracker(max_failures=3, rate_limit_secs=3600)

    async def stalled_spawn(cmd, cwd, timeout):
        return WakeTurnResult(exit_code=0, timed_out=False)

    await process_agent(
        "agent-foo", live_db, locks, cache, tracker,
        spawn_fn=stalled_spawn, claude_bin="claude", prompt="drain",
        timeout=300, user_avatar="user",
    )
    status_count = live_db._conn.execute(
        "SELECT COUNT(*) c FROM messages WHERE content_type='application/json'"
    ).fetchone()["c"]
    assert status_count == 0


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
