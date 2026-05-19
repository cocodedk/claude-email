"""Tests for wake_watcher helpers and main loop."""
import asyncio

import pytest

from src.wake_spawn import WakeTurnResult
from src.wake_watcher import run_wake_watcher
from tests._wake_watcher_helpers import _cfg, live_db  # noqa: F401


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
