"""Tests for the per-agent periodic tick (wake turns on an empty inbox)."""
import asyncio

import pytest

from src.wake_spawn import WakeTurnResult
from src.wake_watcher import run_wake_watcher
from tests._wake_watcher_helpers import _cfg, live_db  # noqa: F401


def test_set_agent_tick_roundtrip(live_db, tmp_path):
    live_db.register_agent("agent-tick", str(tmp_path))
    live_db.set_agent_tick("agent-tick", 300)
    rows = live_db.get_tick_candidates()
    assert [(r["name"], r["tick_secs"]) for r in rows] == [("agent-tick", 300)]
    live_db.set_agent_tick("agent-tick", None)
    assert live_db.get_tick_candidates() == []


@pytest.mark.asyncio
async def test_tick_wakes_agent_with_empty_inbox(live_db, tmp_path):
    live_db.register_agent("agent-tick", str(tmp_path))
    live_db.set_agent_tick("agent-tick", 0)

    prompts: list[str] = []

    async def fake_spawn(cmd, cwd, timeout):
        prompts.append(cmd[-1])
        return WakeTurnResult(exit_code=0, timed_out=False)

    stop = asyncio.Event()
    task = asyncio.create_task(run_wake_watcher(
        live_db, _cfg(tick_prompt="tick!"), stop, spawn_fn=fake_spawn,
    ))
    await asyncio.sleep(0.25)
    stop.set()
    await asyncio.wait_for(task, timeout=2)
    assert prompts and all(p == "tick!" for p in prompts)


@pytest.mark.asyncio
async def test_tick_success_is_not_counted_as_stall(live_db, tmp_path):
    live_db.register_agent("agent-tick", str(tmp_path))
    live_db.set_agent_tick("agent-tick", 0)

    async def fake_spawn(cmd, cwd, timeout):
        return WakeTurnResult(exit_code=0, timed_out=False)

    stop = asyncio.Event()
    task = asyncio.create_task(run_wake_watcher(
        live_db, _cfg(max_failures=1), stop, spawn_fn=fake_spawn,
    ))
    await asyncio.sleep(0.25)
    stop.set()
    await asyncio.wait_for(task, timeout=2)
    # A stall would mark messages failed and emit a wake-watcher email after
    # max_failures=1; assert neither happened.
    events = live_db._conn.execute(
        "SELECT summary FROM events WHERE participant='agent-tick'"
        " AND event_type='wake_spawn_end'",
    ).fetchall()
    assert events, "tick turn should have run"
    mails = live_db.get_pending_messages_for("user")
    assert not any("persistent spawn failure" in m["body"] for m in mails)


@pytest.mark.asyncio
async def test_agent_without_tick_not_woken_on_empty_inbox(live_db, tmp_path):
    live_db.register_agent("agent-quiet", str(tmp_path))

    calls: list[str] = []

    async def fake_spawn(cmd, cwd, timeout):
        calls.append(cwd)
        return WakeTurnResult(exit_code=0, timed_out=False)

    stop = asyncio.Event()
    task = asyncio.create_task(
        run_wake_watcher(live_db, _cfg(), stop, spawn_fn=fake_spawn),
    )
    await asyncio.sleep(0.2)
    stop.set()
    await asyncio.wait_for(task, timeout=2)
    assert calls == []


@pytest.mark.asyncio
async def test_tick_not_due_within_interval(live_db, tmp_path):
    live_db.register_agent("agent-tick", str(tmp_path))
    live_db.set_agent_tick("agent-tick", 3600)
    live_db.upsert_wake_session("agent-tick", "sess-1")  # fresh last_turn_at

    calls: list[str] = []

    async def fake_spawn(cmd, cwd, timeout):
        calls.append(cwd)
        return WakeTurnResult(exit_code=0, timed_out=False)

    stop = asyncio.Event()
    task = asyncio.create_task(
        run_wake_watcher(live_db, _cfg(), stop, spawn_fn=fake_spawn),
    )
    await asyncio.sleep(0.2)
    stop.set()
    await asyncio.wait_for(task, timeout=2)
    assert calls == []
