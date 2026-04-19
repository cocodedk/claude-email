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
    Tool(
        name="chat_enqueue_task",
        description=(
            "Queue a task for a project. A per-project worker is spawned on "
            "demand (one per canonical path) and drains the queue in FIFO "
            "order, except higher-priority tasks jump the line."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Folder name or absolute path",
                },
                "body": {
                    "type": "string",
                    "description": "The task instruction handed to claude --continue --print",
                },
                "priority": {
                    "type": "integer",
                    "description": "Higher runs first; default 0",
                    "default": 0,
                },
            },
            "required": ["project", "body"],
        },
    ),
    Tool(
        name="chat_cancel_task",
        description=(
            "Cancel the currently running task for a project. Sends SIGTERM "
            "then SIGKILL after a 10-second grace. Optionally drains all "
            "pending tasks too."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Folder name or absolute path"},
                "drain_queue": {
                    "type": "boolean",
                    "description": "Also cancel all pending tasks",
                    "default": False,
                },
            },
            "required": ["project"],
        },
    ),
    Tool(
        name="chat_queue_status",
        description=(
            "Return the running task and pending queue for a project."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Folder name or absolute path"},
            },
            "required": ["project"],
        },
    ),
    Tool(
        name="chat_reset_project",
        description=(
            "Step 1 of destructive project reset. Returns a confirm_token "
            "valid for 5 minutes. Call chat_confirm_reset with the same "
            "project and token to actually cancel the running task, drain "
            "the queue, and run `git reset --hard HEAD && git clean -fd`."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Folder name or absolute path"},
            },
            "required": ["project"],
        },
    ),
    Tool(
        name="chat_confirm_reset",
        description=(
            "Step 2 of destructive project reset. Consumes the confirm_token "
            "from chat_reset_project and executes the reset."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Folder name or absolute path"},
                "token": {"type": "string", "description": "Token from chat_reset_project"},
            },
            "required": ["project", "token"],
        },
    ),
    Tool(
        name="chat_where_am_i",
        description=(
            "Cross-project dashboard. Returns one entry per project with a "
            "running-task snapshot, pending count, worker liveness, and last "
            "activity timestamp. Use when the user asks 'what's going on' or "
            "'what are you doing' without naming a project."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
]
