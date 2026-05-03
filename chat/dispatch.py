"""Tool-call dispatcher for the claude-chat MCP server.

Routes a tool name + arguments to the matching chat.tools function,
sanitizing string inputs first. Extracted from chat/server.py so that
module stays under the 200-line cap.
"""
import os

from src.chat_db import ChatDB
from src.reset_control import TokenStore
from src.task_queue import TaskQueue
from src.worker_manager import WorkerManager
from chat import tools

_MAX_NAME_LEN = 128
_MAX_MSG_LEN = 100_000
_MAX_PATH_LEN = 4096


def _parse_task_id(arguments: dict) -> int | None:
    raw = arguments.get("task_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _parse_bool(value, default: bool = False) -> bool:
    """Coerce an MCP argument to bool.

    ``bool("false")`` is True in Python — so a literal string "false"
    flowing in from a JSON-RPC client would silently become a truthy
    flag. Accept common string spellings explicitly and fall back to
    ``default`` for anything unrecognised.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "1", "yes", "y", "on"):
            return True
        if v in ("false", "0", "no", "n", "off", ""):
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _sanitize_str(value: str, max_len: int, field: str) -> str:
    """Validate and strip a string parameter."""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    value = value.strip()
    if not value:
        raise ValueError(f"{field} must not be empty")
    if len(value) > max_len:
        raise ValueError(f"{field} exceeds {max_len} chars")
    return value


def _heartbeat(db: ChatDB, arguments: dict) -> None:
    """Refresh last_seen_at for the caller on every tool invocation.

    Before this hook existed, db.touch_agent only fired on
    chat_check_messages — so an agent that only sent messages (never
    polled) looked stale to the dashboard. Silent no-op when the caller
    isn't registered yet (chat_register itself still writes last_seen_at
    via its INSERT, so we don't need to special-case it).
    """
    caller = arguments.get("_caller")
    if isinstance(caller, str) and caller.strip():
        try:
            db.touch_agent(caller.strip())
        except Exception:
            pass  # never let telemetry block a real tool call


async def dispatch(
    db: ChatDB, queue: TaskQueue, manager: WorkerManager, tokens: TokenStore,
    name: str, arguments: dict,
) -> dict:
    """Route a tool call to the appropriate chat.tools function."""
    _heartbeat(db, arguments)
    if name == "chat_register":
        return tools.register_agent(
            db,
            _sanitize_str(arguments["name"], _MAX_NAME_LEN, "name"),
            _sanitize_str(arguments["project_path"], _MAX_PATH_LEN, "project_path"),
        )
    if name == "chat_ask":
        return await tools.ask_user(
            db,
            _sanitize_str(arguments["_caller"], _MAX_NAME_LEN, "_caller"),
            _sanitize_str(arguments["message"], _MAX_MSG_LEN, "message"),
            task_id=_parse_task_id(arguments),
        )
    if name == "chat_notify":
        return tools.notify_user(
            db,
            _sanitize_str(arguments["_caller"], _MAX_NAME_LEN, "_caller"),
            _sanitize_str(arguments["message"], _MAX_MSG_LEN, "message"),
            task_id=_parse_task_id(arguments),
            progress=arguments.get("progress"),
        )
    if name == "chat_message_agent":
        return tools.message_agent(
            db,
            _sanitize_str(arguments["_caller"], _MAX_NAME_LEN, "_caller"),
            _sanitize_str(arguments["to_agent"], _MAX_NAME_LEN, "to_agent"),
            _sanitize_str(arguments["message"], _MAX_MSG_LEN, "message"),
            task_id=_parse_task_id(arguments),
        )
    if name == "chat_check_messages":
        return tools.check_messages(
            db, _sanitize_str(arguments["_caller"], _MAX_NAME_LEN, "_caller"),
        )
    if name == "chat_list_agents":
        return tools.list_agents(db)
    if name == "chat_deregister":
        return tools.deregister_agent(
            db, _sanitize_str(arguments["_caller"], _MAX_NAME_LEN, "_caller"),
        )
    if name == "chat_spawn_agent":
        return tools.spawn_agent_tool(
            db,
            project=_sanitize_str(arguments["project"], _MAX_PATH_LEN, "project"),
            instruction=arguments.get("instruction", ""),
            chat_url=os.environ.get("CHAT_URL", ""),
            claude_bin=os.environ.get("CLAUDE_BIN", "claude"),
            allowed_base=os.environ.get("CLAUDE_CWD", ""),
            yolo=os.environ.get("CLAUDE_YOLO", "") == "1",
            model=os.environ.get("CLAUDE_MODEL") or None,
            effort=os.environ.get("CLAUDE_EFFORT") or None,
            max_budget_usd=os.environ.get("CLAUDE_MAX_BUDGET_USD") or None,
        )
    if name == "chat_enqueue_task":
        # ``origin_*`` are NOT accepted from MCP arguments — trusting them
        # would let any bus client hijack a task's reply address. Only
        # ``dispatch_token`` is forwarded: it's an opaque correlation
        # marker the email-router passes from the trusted env var
        # ``$CLAUDE_EMAIL_DISPATCH_TOKEN``. claude-email's post-execute
        # fixup uses it to find the freshly-created task and stamp
        # origin_* from the trusted inbound headers.
        return tools.enqueue_task_tool(
            queue, manager,
            project=_sanitize_str(arguments["project"], _MAX_PATH_LEN, "project"),
            body=_sanitize_str(arguments["body"], _MAX_MSG_LEN, "body"),
            priority=int(arguments.get("priority", 0)),
            plan_first=_parse_bool(arguments.get("plan_first", False)),
            # ``.strip()`` because the LLM commonly reads the token via
            # ``echo "$CLAUDE_EMAIL_DISPATCH_TOKEN"`` which appends a
            # newline; persisting the raw \n breaks the fixup's exact
            # match against the un-newlined UUID minted in main.py.
            dispatch_token=str(arguments.get("dispatch_token", "") or "").strip()[:64],
            allowed_base=os.environ.get("CLAUDE_CWD", ""),
        )
    if name == "chat_cancel_task":
        return tools.cancel_task_tool(
            queue,
            project=_sanitize_str(arguments["project"], _MAX_PATH_LEN, "project"),
            drain_queue=_parse_bool(arguments.get("drain_queue", False)),
            allowed_base=os.environ.get("CLAUDE_CWD", ""),
        )
    if name == "chat_queue_status":
        return tools.queue_status_tool(
            queue,
            project=_sanitize_str(arguments["project"], _MAX_PATH_LEN, "project"),
            allowed_base=os.environ.get("CLAUDE_CWD", ""),
        )
    if name == "chat_reset_project":
        return tools.reset_project_tool(
            tokens,
            project=_sanitize_str(arguments["project"], _MAX_PATH_LEN, "project"),
            allowed_base=os.environ.get("CLAUDE_CWD", ""),
        )
    if name == "chat_confirm_reset":
        return tools.confirm_reset_tool(
            queue, tokens,
            project=_sanitize_str(arguments["project"], _MAX_PATH_LEN, "project"),
            token=_sanitize_str(arguments["token"], _MAX_NAME_LEN, "token"),
            allowed_base=os.environ.get("CLAUDE_CWD", ""),
        )
    if name == "chat_retry_task":
        return tools.retry_task_tool(
            queue, manager,
            task_id=int(arguments["task_id"]),
            new_body=str(arguments.get("new_body", "")),
        )
    if name == "chat_where_am_i":
        return tools.where_am_i_tool(queue, manager)
    if name == "chat_commit_project":
        return tools.commit_project_tool(
            project=_sanitize_str(arguments["project"], _MAX_PATH_LEN, "project"),
            message=_sanitize_str(arguments["message"], _MAX_MSG_LEN, "message"),
            push=_parse_bool(arguments.get("push", False)),
            allowed_base=os.environ.get("CLAUDE_CWD", ""),
        )
    raise ValueError(f"Unknown tool: {name}")
