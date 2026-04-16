"""MCP tool handler functions for the claude-chat relay.

Pure business-logic layer between MCP server and ChatDB.
No MCP dependencies, no network — just logic + DB.
"""
import asyncio
from src.chat_db import ChatDB


def register_agent(db: ChatDB, name: str, project_path: str) -> dict:
    """Register an agent with the given name and project path."""
    db.register_agent(name, project_path)
    return {"status": "registered", "name": name}


def notify_user(db: ChatDB, caller: str, message: str) -> dict:
    """Send a one-way notification from caller to user."""
    db.insert_message(caller, "user", message, "notify")
    return {"status": "sent"}


async def ask_user(
    db: ChatDB, caller: str, message: str, *, poll_interval: float = 2.0,
) -> dict:
    """Send a question to user and block until user replies.

    Creates an ask message, then polls for a reply every poll_interval
    seconds until one appears.
    """
    msg = db.insert_message(caller, "user", message, "ask")
    msg_id = msg["id"]
    while True:
        reply = db.get_reply_to_message(msg_id)
        if reply is not None:
            return {"reply": reply["body"]}
        await asyncio.sleep(poll_interval)


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
