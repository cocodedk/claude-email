"""Project-scoped tool definitions (spawn/enqueue/cancel/reset/commit/status).

Split from tool_definitions.py so both files stay under the 200-line cap
as Phase 5 added more tools.
"""
from mcp.types import Tool

_PATH_DESC = "Folder name or absolute path"

PROJECT_TOOLS = [
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
                "project": {"type": "string", "description": _PATH_DESC},
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
                "project": {"type": "string", "description": _PATH_DESC},
                "body": {
                    "type": "string",
                    "description": "The task instruction handed to claude --continue --print",
                },
                "priority": {
                    "type": "integer",
                    "description": "Higher runs first; default 0",
                    "default": 0,
                },
                "plan_first": {
                    "type": "boolean",
                    "description": (
                        "When true, the worker's claude MUST propose a "
                        "plan via chat_ask and wait for user approval "
                        "before making any changes. Use for vague/big "
                        "requests ('review', 'audit', 'refactor X', "
                        "'analyze Y') where scope control matters."
                    ),
                    "default": False,
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
                "project": {"type": "string", "description": _PATH_DESC},
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
        description="Return the running task and pending queue for a project.",
        inputSchema={
            "type": "object",
            "properties": {"project": {"type": "string", "description": _PATH_DESC}},
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
            "properties": {"project": {"type": "string", "description": _PATH_DESC}},
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
                "project": {"type": "string", "description": _PATH_DESC},
                "token": {"type": "string", "description": "Token from chat_reset_project"},
            },
            "required": ["project", "token"],
        },
    ),
    Tool(
        name="chat_commit_project",
        description=(
            "Commit any pending changes in a project. Escape hatch for a "
            "dirty repo that would otherwise fail the branch-per-task "
            "guard. No claude subprocess is started — just `git add -A && "
            "git commit -m <message>`, optionally followed by `git push`. "
            "Use when the user emails 'commit the current changes' / 'save "
            "what's there' / 'WIP commit' / 'commit and push'."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": _PATH_DESC},
                "message": {"type": "string", "description": "Commit message"},
                "push": {
                    "type": "boolean",
                    "description": (
                        "Also run `git push` on the current branch after "
                        "the commit. Set true when the user asked to push."
                    ),
                    "default": False,
                },
            },
            "required": ["project", "message"],
        },
    ),
    Tool(
        name="chat_retry_task",
        description=(
            "Re-enqueue a previously terminal task (done / failed / "
            "cancelled). Pass task_id; optionally pass new_body to refine. "
            "Preserves priority and project; records the chain via retry_of "
            "so the audit log shows lineage."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "Original task id"},
                "new_body": {
                    "type": "string",
                    "description": "Refined instruction (default: reuse original body)",
                    "default": "",
                },
            },
            "required": ["task_id"],
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
