"""MCP Tool definitions (name/description/schema) for the claude-chat server.

Kept separate from server.py so the dispatch and transport code stay small.
"""
from mcp.types import Tool

_CALLER_PROP = {"type": "string", "description": "Registered agent name"}

TOOLS = [
    Tool(
        name="chat_register",
        description="Register an agent with the chat relay.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Agent name"},
                "project_path": {
                    "type": "string",
                    "description": "Absolute path to the agent project",
                },
            },
            "required": ["name", "project_path"],
        },
    ),
    Tool(
        name="chat_ask",
        description="Send a question to the user and wait for a reply.",
        inputSchema={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Question text"},
                "_caller": _CALLER_PROP,
            },
            "required": ["message", "_caller"],
        },
    ),
    Tool(
        name="chat_notify",
        description="Send a one-way notification to the user.",
        inputSchema={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Notification text"},
                "_caller": _CALLER_PROP,
            },
            "required": ["message", "_caller"],
        },
    ),
    Tool(
        name="chat_check_messages",
        description="Return pending messages for the caller agent.",
        inputSchema={
            "type": "object",
            "properties": {"_caller": _CALLER_PROP},
            "required": ["_caller"],
        },
    ),
    Tool(
        name="chat_list_agents",
        description="List all registered agents.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="chat_deregister",
        description="Deregister the caller agent.",
        inputSchema={
            "type": "object",
            "properties": {"_caller": _CALLER_PROP},
            "required": ["_caller"],
        },
    ),
    Tool(
        name="chat_spawn_agent",
        description=(
            "Spawn a new Claude Code agent in a project directory. "
            "project can be a bare folder name (resolved against the server's "
            "allowed-base) or an absolute path."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Folder name or absolute path",
                },
                "instruction": {
                    "type": "string",
                    "description": "Optional task to hand to the spawned agent",
                },
            },
            "required": ["project"],
        },
    ),
]
