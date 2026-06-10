"""Wake watcher: asyncio task that drives `claude --print` turns for agents
with pending bus messages. Lives inside the claude-chat.service process
alongside the MCP SSE app.

Helpers live in wake_helpers.py; this module holds the orchestrator
(`process_agent`) and the main loop (`run_wake_watcher`).
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass

from src.chat_db import ChatDB
from src.status_envelope import clear_status_dedup_for_project
from src.wake_helpers import (
    _AgentLocks, _FailureTracker, _SessionCache, _handle_failure,
    _has_live_owner, _is_session_fresh, tick_due_names,
)
from src.wake_spawn import WakeTurnResult, build_wake_cmd

logger = logging.getLogger(__name__)

# re-exports for tests and external callers
__all__ = [
    "WakeWatcherConfig", "_AgentLocks", "_FailureTracker", "_SessionCache",
    "process_agent", "run_wake_watcher",
]


@dataclass
class WakeWatcherConfig:
    interval_secs: float
    timeout_secs: float
    idle_expiry_secs: float
    max_failures: int
    rate_limit_secs: float
    claude_bin: str
    prompt: str
    user_avatar: str
    tick_prompt: str = (
        "[watcher tick] No new messages. If you have an open task with "
        "uncommitted work, continue it now; commit a coherent green slice "
        "before expanding. If you are idle, reply the single word \"quiet\"."
    )


async def process_agent(
    agent_name: str, db: ChatDB, locks: _AgentLocks, cache: _SessionCache,
    tracker: _FailureTracker, *, spawn_fn, claude_bin: str, prompt: str,
    timeout: float, user_avatar: str, tick: bool = False,
) -> None:
    """Drive one wake turn for one agent. Safe to call from the loop."""
    if not await locks.try_acquire(agent_name):
        return
    try:
        agent = db.get_agent(agent_name)
        if not agent or not agent.get("project_path"):
            logger.warning("wake: unknown/path-less agent %s", agent_name)
            return
        project_path = agent["project_path"]

        pre_ids = {m["id"] for m in db.get_pending_messages_for(agent_name)}
        if not pre_ids and not tick:
            # Drained by another consumer between recipient scan and entry.
            return

        if agent.get("status") != "running":
            # Reaped — spawn would stall. recover_failed_messages_for re-pendings on re-register.
            for mid in pre_ids:
                db.mark_message_failed(mid)
            return
        if _has_live_owner(agent):
            return  # live session's own hook drain wins; don't race it

        cached = cache.get(agent_name)
        if cached is None:
            persisted = db.get_wake_session(agent_name)
            if persisted and _is_session_fresh(persisted, cache.idle_secs):
                cached = persisted["session_id"]
            elif persisted:
                # Older than idle_expiry — drop it; next turn creates a
                # fresh session-id rather than resuming an expired one.
                db.delete_wake_session(agent_name)
        is_resume = cached is not None
        session_id = cached or str(uuid.uuid4())

        cmd = build_wake_cmd(claude_bin, session_id, is_resume, prompt)
        summary = f"resume={is_resume} pending={len(pre_ids)}"
        db._log_event(agent_name, "wake_spawn_start", summary + (" tick=True" if tick else ""))
        result = await spawn_fn(cmd, cwd=project_path, timeout=timeout)
        exit_code = getattr(result, "exit_code", None)
        timed_out = getattr(result, "timed_out", "?")
        db._log_event(agent_name, "wake_spawn_end", f"exit={exit_code} timeout={timed_out}")
        if isinstance(result, WakeTurnResult) and result.exit_code == 0:
            # Cache the session even on stall — --resume keeps the prompt cache warm.
            cache.set(agent_name, session_id)
            db.upsert_wake_session(agent_name, session_id)
            post_ids = {m["id"] for m in db.get_pending_messages_for(agent_name)}
            if pre_ids and pre_ids <= post_ids:
                # No pre-spawn message delivered — stall. Escalates after max_failures.
                stall = WakeTurnResult(
                    exit_code=0, timed_out=False,
                    error=f"no progress ({len(pre_ids)} stuck)",
                )
                _handle_failure(db, tracker, agent_name, project_path, stall, user_avatar)
            else:
                tracker.record_success(agent_name)
                clear_status_dedup_for_project(db, project_path)
        else:
            _handle_failure(db, tracker, agent_name, project_path, result, user_avatar)
    finally:
        locks.release(agent_name)


async def run_wake_watcher(
    db: ChatDB, cfg: WakeWatcherConfig, stop: asyncio.Event, *, spawn_fn,
    nudge: asyncio.Event | None = None,
) -> None:
    """Poll for pending recipients and drive wake turns until stop is set.

    When `nudge` is provided, writers (e.g. ChatDB.insert_message) can set it
    to wake the loop immediately instead of waiting for `cfg.interval_secs`.
    The nudge is cleared after each wake so the next sleep is full-duration.
    """
    locks = _AgentLocks()
    cache = _SessionCache(idle_secs=cfg.idle_expiry_secs)
    tracker = _FailureTracker(
        max_failures=cfg.max_failures, rate_limit_secs=cfg.rate_limit_secs,
    )
    logger.info("wake watcher started (interval=%.2fs)", cfg.interval_secs)
    while not stop.is_set():
        try:
            recipients = db.get_distinct_pending_recipients()
        except Exception:
            logger.exception("wake: recipient query failed")
            recipients = []
        recipients = [r for r in recipients if r != cfg.user_avatar]
        try:
            ticks = tick_due_names(
                db, exclude=set(recipients) | {cfg.user_avatar}, tracker=tracker,
            )
        except Exception:
            logger.exception("wake: tick query failed")
            ticks = []
        jobs = [(r, cfg.prompt, False) for r in recipients]
        jobs += [(t, cfg.tick_prompt, True) for t in ticks]
        results = await asyncio.gather(*[
            process_agent(
                name, db, locks, cache, tracker,
                spawn_fn=spawn_fn, claude_bin=cfg.claude_bin,
                prompt=job_prompt, timeout=cfg.timeout_secs,
                user_avatar=cfg.user_avatar, tick=is_tick,
            )
            for name, job_prompt, is_tick in jobs
        ], return_exceptions=True)
        for (recipient, _prompt, _tick), result in zip(jobs, results):
            if isinstance(result, Exception):
                logger.error(
                    "wake: process_agent failed for %s", recipient,
                    exc_info=result,
                )
        waiter = nudge.wait() if nudge is not None else stop.wait()
        try:
            await asyncio.wait_for(waiter, timeout=cfg.interval_secs)
        except asyncio.TimeoutError:
            pass
        if nudge is not None:
            nudge.clear()
    logger.info("wake watcher stopped")
