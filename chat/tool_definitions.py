"""MCP Tool definitions (name/description/schema) for the claude-chat server.

Core chat-bus tools (register/ask/notify/etc.) live here. Project-scoped
tools (spawn/enqueue/cancel/reset/commit/status) live in
chat/project_tool_defs.py so both files stay under the 200-line cap.
"""
from mcp.types import Tool

from chat.project_tool_defs import PROJECT_TOOLS

_CALLER_PROP = {"type": "string", "description": "Registered agent name"}

_CORE_TOOLS = [
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
]

TOOLS = _CORE_TOOLS + PROJECT_TOOLS
