"""Per-project MCP tools: enqueue_task / cancel / status / reset flow.

Split out of chat/tools.py so that file stays under the 200-line cap as
Phase 3 grew. Shares one resolve_project helper — canonical-path
resolution is the seatbelt that keeps the router from touching anything
outside CLAUDE_CWD.
"""
import os
from pathlib import Path

from src.error_codes import (
    ProjectNotFound, ProjectOutsideBase, error_result_from_exc,
)
from src.git_ops import task_branch_name
from src.task_control import cancel_running_task, queue_status
from src.task_queue import TaskQueue
from src.worker_manager import WorkerManager

_MIN_PRIORITY = 0
_MAX_PRIORITY = 10


def _clamp_priority(priority: int) -> int:
    return max(_MIN_PRIORITY, min(_MAX_PRIORITY, priority))


def resolve_project(project: str, allowed_base: str) -> str:
    if not allowed_base:
        raise ValueError("CLAUDE_CWD not configured on chat server")
    candidate = project if os.path.isabs(project) else os.path.join(allowed_base, project)
    resolved = str(Path(candidate).resolve())
    if not os.path.isdir(resolved):
        raise ProjectNotFound(f"Project path does not exist: {resolved}")
    base = str(Path(allowed_base).resolve())
    if not resolved.startswith(base + os.sep) and resolved != base:
        raise ProjectOutsideBase(f"Project path {resolved} is outside allowed base {base}")
    return resolved


def enqueue_task_tool(
    queue: TaskQueue, manager: WorkerManager, *,
    project: str, body: str, priority: int = 0,
    allowed_base: str, plan_first: bool = False,
    origin_content_type: str = "", origin_message_id: str = "",
    origin_subject: str = "", origin_from: str = "",
    dispatch_token: str = "",
) -> dict:
    try:
        resolved = resolve_project(project, allowed_base)
    except ValueError as exc:
        return error_result_from_exc(exc)
    try:
        worker_pid = manager.ensure_worker(resolved)
    except ValueError as exc:
        return error_result_from_exc(exc)
    task_id = queue.enqueue(
        resolved, body, priority=_clamp_priority(priority), plan_first=plan_first,
        origin_content_type=origin_content_type,
        origin_message_id=origin_message_id, origin_subject=origin_subject,
        origin_from=origin_from, dispatch_token=dispatch_token,
    )
    return {
        "status": "enqueued",
        "task_id": task_id,
        "worker_pid": worker_pid,
        "planned_branch": task_branch_name(task_id, body),
        "plan_first": plan_first,
    }


def cancel_task_tool(
    queue: TaskQueue, *, project: str, allowed_base: str,
    drain_queue: bool = False,
) -> dict:
    try:
        resolved = resolve_project(project, allowed_base)
    except ValueError as exc:
        return error_result_from_exc(exc)
    return cancel_running_task(queue, resolved, drain_queue=drain_queue)


def queue_status_tool(
    queue: TaskQueue, *, project: str, allowed_base: str,
) -> dict:
    try:
        resolved = resolve_project(project, allowed_base)
    except ValueError as exc:
        return error_result_from_exc(exc)
    return queue_status(queue, resolved)


_TERMINAL_STATES = {"done", "failed", "cancelled"}


def retry_task_tool(
    queue: TaskQueue, manager: WorkerManager, *,
    task_id: int, new_body: str = "",
) -> dict:
    """Re-enqueue a previously terminated task.

    Body defaults to the original task's body. When new_body is given, it
    replaces — useful for 'retry but also do X' refinements. A retry chain
    is recorded via tasks.retry_of so the audit log can show lineage.
    """
    original = queue.get(task_id)
    if original is None:
        return {"error": f"task #{task_id} not found"}
    status = original.get("status")
    if status not in _TERMINAL_STATES:
        return {"error": f"task #{task_id} is {status}; can only retry terminal tasks"}
    body = new_body.strip() or original["body"]
    project_path = original["project_path"]
    try:
        worker_pid = manager.ensure_worker(project_path)
    except ValueError as exc:
        return {"error": str(exc)}
    new_id = queue.enqueue(
        project_path, body,
        priority=_clamp_priority(original.get("priority") or 0),
        retry_of=task_id,
    )
    return {
        "status": "retried",
        "new_task_id": new_id,
        "retry_of": task_id,
        "worker_pid": worker_pid,
    }


def where_am_i_tool(queue: TaskQueue, manager: WorkerManager) -> dict:
    """Cross-project dashboard: one row per project with recent activity."""
    projects = []
    for path in queue.list_project_paths():
        running = queue.get_running(path)
        pending = queue.list_pending(path)
        latest = queue.latest_task(path)
        projects.append({
            "project_path": path,
            "project_name": Path(path).name,
            "worker_pid": manager.pid_of(path),
            "running_task": running,
            "pending_count": len(pending),
            "last_task_status": (latest or {}).get("status"),
            "last_activity_at": (
                (latest or {}).get("completed_at")
                or (latest or {}).get("started_at")
                or (latest or {}).get("created_at")
            ),
        })
    return {"projects": projects}
