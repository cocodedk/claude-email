"""MCP SSE server for the claude-chat relay.

Creates a Starlette app with MCP SSE transport and wires up
tool handlers that delegate to chat.tools functions.
"""
import json
import logging
import os

from mcp.server.lowlevel import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent
from starlette.applications import Starlette
from starlette.responses import Response
from starlette.routing import Mount, Route

from src.chat_db import ChatDB
from src.task_queue import TaskQueue
from src.worker_manager import WorkerManager
from chat import tools
from chat.tool_definitions import TOOLS

logger = logging.getLogger(__name__)


# ── App factory ─────────────────────────────────────────────────

def create_app(db_path: str, host: str, port: int) -> Starlette:
    """Build a Starlette app with MCP SSE transport and tool handlers."""
    db = ChatDB(db_path)
    queue = TaskQueue(db_path)
    manager = WorkerManager(
        db_path=db_path,
        project_root=os.environ.get("CLAUDE_CWD") or os.getcwd(),
    )
    server = Server("claude-chat", version="1.0")
    sse = SseServerTransport("/messages/")

    # ── list_tools handler ──────────────────────────────────
    @server.list_tools()
    async def handle_list_tools() -> list[Tool]:
        return TOOLS

    # ── call_tool handler ───────────────────────────────────
    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict) -> list[TextContent]:
        result = await _dispatch(db, queue, manager, name, arguments)
        return [TextContent(type="text", text=json.dumps(result))]

    # ── SSE endpoint ────────────────────────────────────────
    async def handle_sse(request):
        async with sse.connect_sse(
            request.scope, request.receive, request._send,
        ) as streams:
            await server.run(
                streams[0],
                streams[1],
                server.create_initialization_options(),
            )
        return Response()

    app = Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=sse.handle_post_message),
        ],
    )
    # Expose server on app.state for testing
    app.state.mcp_server = server
    return app


_MAX_NAME_LEN = 128
_MAX_MSG_LEN = 100_000
_MAX_PATH_LEN = 4096


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


async def _dispatch(
    db: ChatDB, queue: TaskQueue, manager: WorkerManager,
    name: str, arguments: dict,
) -> dict:
    """Route a tool call to the appropriate chat.tools function."""
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
        )
    if name == "chat_notify":
        return tools.notify_user(
            db,
            _sanitize_str(arguments["_caller"], _MAX_NAME_LEN, "_caller"),
            _sanitize_str(arguments["message"], _MAX_MSG_LEN, "message"),
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
        return tools.enqueue_task_tool(
            queue, manager,
            project=_sanitize_str(arguments["project"], _MAX_PATH_LEN, "project"),
            body=_sanitize_str(arguments["body"], _MAX_MSG_LEN, "body"),
            priority=int(arguments.get("priority", 0)),
            allowed_base=os.environ.get("CLAUDE_CWD", ""),
        )
    if name == "chat_cancel_task":
        return tools.cancel_task_tool(
            queue,
            project=_sanitize_str(arguments["project"], _MAX_PATH_LEN, "project"),
            drain_queue=bool(arguments.get("drain_queue", False)),
            allowed_base=os.environ.get("CLAUDE_CWD", ""),
        )
    if name == "chat_queue_status":
        return tools.queue_status_tool(
            queue,
            project=_sanitize_str(arguments["project"], _MAX_PATH_LEN, "project"),
            allowed_base=os.environ.get("CLAUDE_CWD", ""),
        )
    raise ValueError(f"Unknown tool: {name}")
