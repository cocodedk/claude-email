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
                "task_id": {"type": "integer", "description": "Task ID this message belongs to (for email threading)"},
                "suggested_replies": {
                    "type": "array",
                    "description": (
                        "Optional list of short suggested replies (≤4 chips, "
                        "≤30 chars each). Renders as tappable chips above the "
                        "composer in the app on JSON-origin tasks. "
                        "Trimmed/dedup'd; invalid entries silently dropped."
                    ),
                    # maxItems must match _REPLY_MAX_COUNT in src/question_envelope.py
                    "maxItems": 4,
                    # maxLength must match _REPLY_MAX_LEN in src/question_envelope.py
                    "items": {"type": "string", "maxLength": 30},
                },
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
                "task_id": {"type": "integer", "description": "Task ID this message belongs to (for email threading)"},
                "progress": {
                    "type": "object",
                    "description": (
                        "Optional progress indicator. Renders as a progress "
                        "bar in the app when present (JSON-origin tasks only). "
                        "All sub-fields optional; invalid entries silently dropped."
                    ),
                    "properties": {
                        "current": {"type": "integer", "minimum": 0},
                        "total": {"type": "integer", "minimum": 1},
                        "percent": {"type": "number", "minimum": 0, "maximum": 100},
                        # maxLength must match _LABEL_MAX in src/progress_envelope.py
                        "label": {"type": "string", "maxLength": 200},
                    },
                },
            },
            "required": ["message", "_caller"],
        },
    ),
    Tool(
        name="chat_message_agent",
        description=(
            "Send a one-way notification from the caller agent to another "
            "registered agent. Use chat_notify instead when the recipient is "
            "the user."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "to_agent": {
                    "type": "string",
                    "description": "Recipient agent name (must be registered)",
                },
                "message": {"type": "string", "description": "Message body"},
                "task_id": {
                    "type": "integer",
                    "description": (
                        "Task ID to thread this message under. When replying "
                        "to a peer message that arrived with a task_id (e.g. "
                        "an email-routed command), echo it here so SMTP relay "
                        "addresses the eventual user-facing reply via "
                        "tasks.origin_from instead of the canonical sender."
                    ),
                },
                "_caller": _CALLER_PROP,
            },
            "required": ["to_agent", "message", "_caller"],
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
