"""MCP tool handler functions for the claude-chat relay.

Pure business-logic layer between MCP server and ChatDB.
No MCP dependencies, no network — just logic + DB.

Per-project queue/cancel/reset tools live in chat/project_tools.py so both
files stay under the 200-line cap.
"""
import asyncio

from src.chat_db import ChatDB
from src.spawner import spawn_agent


def register_agent(db: ChatDB, name: str, project_path: str) -> dict:
    """Register an agent with the given name and project path."""
    db.register_agent(name, project_path)
    return {"status": "registered", "name": name}


def notify_user(db: ChatDB, caller: str, message: str, task_id: int | None = None) -> dict:
    """Send a one-way notification from caller to user."""
    db.insert_message(caller, "user", message, "notify", task_id=task_id)
    return {"status": "sent"}


def message_agent(
    db: ChatDB, caller: str, to_agent: str, message: str,
    task_id: int | None = None,
) -> dict:
    """Send a one-way notification from caller to another registered agent.

    Rejects to_agent=='user' — callers should use notify_user/chat_notify
    for that. Rejects unknown recipients so typos don't silently queue
    ghost messages that will never be drained. ``task_id`` is forwarded
    so agent-to-agent replies can thread back to the originating task,
    matching notify_user/ask_user behaviour.
    """
    if not to_agent:
        return {"error": "to_agent must not be empty"}
    if to_agent == "user":
        return {"error": "to_agent='user' is not allowed — use chat_notify to reach the user"}
    if db.get_agent(to_agent) is None:
        return {"error": f"no registered agent named {to_agent!r}"}
    db.insert_message(caller, to_agent, message, "notify", task_id=task_id)
    return {"status": "sent", "to": to_agent}


_ASK_TIMEOUT = 3600  # 1 hour max wait


async def ask_user(
    db: ChatDB, caller: str, message: str, *,
    poll_interval: float = 2.0, timeout: float = _ASK_TIMEOUT,
    task_id: int | None = None,
) -> dict:
    """Send a question to user and block until user replies.

    Creates an ask message, then polls for a reply every poll_interval
    seconds until one appears or timeout is reached.
    """
    msg = db.insert_message(caller, "user", message, "ask", task_id=task_id)
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
    pending = db.claim_pending_messages_for(caller)
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


# Re-export Phase 3 project tools so dispatch + callers can use a single
# `from chat import tools` import surface.
from chat.project_tools import (  # noqa: E402
    cancel_task_tool,
    commit_project_tool,
    confirm_reset_tool,
    enqueue_task_tool,
    queue_status_tool,
    reset_project_tool,
    retry_task_tool,
    where_am_i_tool,
)
