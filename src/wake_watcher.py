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
from src.wake_helpers import _AgentLocks, _FailureTracker, _SessionCache
from src.wake_spawn import WakeTurnResult, build_wake_cmd

logger = logging.getLogger(__name__)

# re-exports for tests and external callers
__all__ = [
    "WakeWatcherConfig",
    "_AgentLocks",
    "_FailureTracker",
    "_SessionCache",
    "process_agent",
    "run_wake_watcher",
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


async def process_agent(
    agent_name: str, db: ChatDB, locks: _AgentLocks, cache: _SessionCache,
    tracker: _FailureTracker, *, spawn_fn, claude_bin: str, prompt: str,
    timeout: float, user_avatar: str,
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

        cached = cache.get(agent_name)
        if cached is None:
            persisted = db.get_wake_session(agent_name)
            cached = persisted["session_id"] if persisted else None
        is_resume = cached is not None
        session_id = cached or str(uuid.uuid4())

        cmd = build_wake_cmd(claude_bin, session_id, is_resume, prompt)
        result = await spawn_fn(cmd, cwd=project_path, timeout=timeout)

        if isinstance(result, WakeTurnResult) and result.exit_code == 0:
            cache.set(agent_name, session_id)
            db.upsert_wake_session(agent_name, session_id)
            tracker.record_success(agent_name)
        else:
            _handle_failure(
                db, tracker, agent_name, project_path, result, user_avatar,
            )
    finally:
        locks.release(agent_name)


def _handle_failure(
    db: ChatDB, tracker: _FailureTracker, agent_name: str, project_path: str,
    result, user_avatar: str,
) -> None:
    tracker.record_failure(agent_name)
    logger.warning(
        "wake: turn failed for %s (exit=%s timeout=%s error=%s)",
        agent_name,
        getattr(result, "exit_code", "?"),
        getattr(result, "timed_out", "?"),
        getattr(result, "error", None),
    )
    if not tracker.should_escalate(agent_name):
        return
    if not tracker.can_notify(agent_name):
        return
    pending = db.get_pending_messages_for(agent_name)
    body = (
        f"[wake-watcher] persistent spawn failure\n"
        f"agent: {agent_name}\n"
        f"project: {project_path}\n"
        f"stuck messages: {len(pending)}\n"
        f"last error: exit={getattr(result, 'exit_code', '?')} "
        f"timeout={getattr(result, 'timed_out', '?')} "
        f"error={getattr(result, 'error', None)}"
    )
    db.insert_message("wake-watcher", user_avatar, body, "notify")
    for m in pending:
        db.mark_message_failed(m["id"])
    tracker.mark_notified(agent_name)


async def run_wake_watcher(
    db: ChatDB, cfg: WakeWatcherConfig, stop: asyncio.Event, *, spawn_fn,
) -> None:
    """Poll for pending recipients and drive wake turns until stop is set."""
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
        await asyncio.gather(*[
            process_agent(
                r, db, locks, cache, tracker,
                spawn_fn=spawn_fn, claude_bin=cfg.claude_bin,
                prompt=cfg.prompt, timeout=cfg.timeout_secs,
                user_avatar=cfg.user_avatar,
            )
            for r in recipients
        ], return_exceptions=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=cfg.interval_secs)
        except asyncio.TimeoutError:
            pass
    logger.info("wake watcher stopped")
