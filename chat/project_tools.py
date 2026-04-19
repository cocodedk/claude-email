"""Per-project MCP tools: enqueue_task / cancel / status / reset flow.

Split out of chat/tools.py so that file stays under the 200-line cap as
Phase 3 grew. Shares one _resolve_project helper — canonical-path
resolution is the seatbelt that keeps the router from touching anything
outside CLAUDE_CWD.
"""
import os
from pathlib import Path

from src.reset_control import TokenStore, perform_reset
from src.task_control import cancel_running_task, queue_status
from src.task_queue import TaskQueue
from src.worker_manager import WorkerManager


def _resolve_project(project: str, allowed_base: str) -> str:
    if not allowed_base:
        raise ValueError("CLAUDE_CWD not configured on chat server")
    candidate = project if os.path.isabs(project) else os.path.join(allowed_base, project)
    resolved = str(Path(candidate).resolve())
    if not os.path.isdir(resolved):
        raise ValueError(f"Project path does not exist: {resolved}")
    base = str(Path(allowed_base).resolve())
    if not resolved.startswith(base + os.sep) and resolved != base:
        raise ValueError(f"Project path {resolved} is outside allowed base {base}")
    return resolved


def enqueue_task_tool(
    queue: TaskQueue, manager: WorkerManager, *,
    project: str, body: str, priority: int = 0,
    allowed_base: str,
) -> dict:
    try:
        resolved = _resolve_project(project, allowed_base)
    except ValueError as exc:
        return {"error": str(exc)}
    try:
        worker_pid = manager.ensure_worker(resolved)
    except ValueError as exc:
        return {"error": str(exc)}
    task_id = queue.enqueue(resolved, body, priority=priority)
    return {"status": "enqueued", "task_id": task_id, "worker_pid": worker_pid}


def cancel_task_tool(
    queue: TaskQueue, *, project: str, allowed_base: str,
    drain_queue: bool = False,
) -> dict:
    try:
        resolved = _resolve_project(project, allowed_base)
    except ValueError as exc:
        return {"error": str(exc)}
    return cancel_running_task(queue, resolved, drain_queue=drain_queue)


def queue_status_tool(
    queue: TaskQueue, *, project: str, allowed_base: str,
) -> dict:
    try:
        resolved = _resolve_project(project, allowed_base)
    except ValueError as exc:
        return {"error": str(exc)}
    return queue_status(queue, resolved)


def reset_project_tool(
    tokens: TokenStore, *, project: str, allowed_base: str,
) -> dict:
    """Step 1 of the two-step hard reset — issues a confirm token."""
    try:
        resolved = _resolve_project(project, allowed_base)
    except ValueError as exc:
        return {"error": str(exc)}
    token = tokens.issue(resolved)
    return {
        "status": "confirm_required",
        "confirm_token": token,
        "project": resolved,
        "instruction": (
            "Reset will cancel the running task, drain the queue, and run "
            "`git reset --hard HEAD && git clean -fd`. To confirm, call "
            "chat_confirm_reset with the same project and this token."
        ),
    }


def confirm_reset_tool(
    queue: TaskQueue, tokens: TokenStore, *,
    project: str, token: str, allowed_base: str,
) -> dict:
    """Step 2 — validate token and perform the destructive reset."""
    try:
        resolved = _resolve_project(project, allowed_base)
    except ValueError as exc:
        return {"error": str(exc)}
    if not tokens.consume(resolved, token):
        return {"error": "invalid or expired confirm token"}
    return perform_reset(queue, resolved)
