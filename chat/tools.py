"""MCP tool handler functions for the claude-chat relay.

Pure business-logic layer between MCP server and ChatDB.
No MCP dependencies, no network — just logic + DB.
"""
import asyncio
import os
from pathlib import Path

from src.chat_db import ChatDB
from src.spawner import spawn_agent
from src.task_control import cancel_running_task, queue_status
from src.task_queue import TaskQueue
from src.worker_manager import WorkerManager


def register_agent(db: ChatDB, name: str, project_path: str) -> dict:
    """Register an agent with the given name and project path."""
    db.register_agent(name, project_path)
    return {"status": "registered", "name": name}


def notify_user(db: ChatDB, caller: str, message: str) -> dict:
    """Send a one-way notification from caller to user."""
    db.insert_message(caller, "user", message, "notify")
    return {"status": "sent"}


_ASK_TIMEOUT = 3600  # 1 hour max wait


async def ask_user(
    db: ChatDB, caller: str, message: str, *,
    poll_interval: float = 2.0, timeout: float = _ASK_TIMEOUT,
) -> dict:
    """Send a question to user and block until user replies.

    Creates an ask message, then polls for a reply every poll_interval
    seconds until one appears or timeout is reached.
    """
    msg = db.insert_message(caller, "user", message, "ask")
    msg_id = msg["id"]
    elapsed = 0.0
    while elapsed < timeout:
        reply = db.get_reply_to_message(msg_id)
        if reply is not None:
            return {"reply": reply["body"]}
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
    return {"error": f"No reply received within {int(timeout)}s"}


def check_messages(db: ChatDB, caller: str) -> dict:
    """Return pending messages for caller and mark them as delivered."""
    db.touch_agent(caller)
    pending = db.get_pending_messages_for(caller)
    for m in pending:
        db.mark_message_delivered(m["id"])
    return {
        "messages": [
            {
                "id": m["id"],
                "from": m["from_name"],
                "body": m["body"],
                "type": m["type"],
                "created_at": m["created_at"],
            }
            for m in pending
        ]
    }


def list_agents(db: ChatDB) -> dict:
    """List all registered agents."""
    agents = db.list_agents()
    return {
        "agents": [
            {
                "name": a["name"],
                "status": a["status"],
                "project_path": a["project_path"],
                "last_seen_at": a["last_seen_at"],
            }
            for a in agents
        ]
    }


def deregister_agent(db: ChatDB, caller: str) -> dict:
    """Mark caller agent as deregistered."""
    db.update_agent_status(caller, "deregistered")
    return {"status": "deregistered"}


def spawn_agent_tool(
    db: ChatDB, *, project: str, instruction: str = "",
    chat_url: str, claude_bin: str, allowed_base: str,
    yolo: bool = False, model: str | None = None,
    effort: str | None = None, max_budget_usd: str | None = None,
) -> dict:
    """Spawn a Claude Code agent in a project directory.

    project may be a bare folder name (resolved against allowed_base) or an
    absolute path. Invalid paths — nonexistent or outside allowed_base — are
    returned as {"error": ...} rather than raised, so the MCP caller sees a
    structured failure.
    """
    if not allowed_base:
        return {"error": "CLAUDE_CWD not configured on chat server"}
    try:
        name, pid = spawn_agent(
            db, project, chat_url,
            instruction=instruction,
            claude_bin=claude_bin,
            allowed_base=allowed_base,
            yolo=yolo,
            model=model,
            effort=effort,
            max_budget_usd=max_budget_usd,
        )
    except ValueError as exc:
        return {"error": str(exc)}
    return {"status": "spawned", "name": name, "pid": pid}


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
    """Queue a task for a project and make sure a worker is running for it."""
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
