"""MCP SSE server for the claude-chat relay.

Creates a Starlette app with MCP SSE transport and wires up
tool handlers that delegate to chat.tools functions.
"""
import asyncio
import contextlib
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
from src.llm_router import ROUTER_MCP_CONFIG_PATH
from src.proc_reconcile import reconcile_live_agents
from src.reset_control import TokenStore
from src.task_queue import TaskQueue
from src.wake_spawn import run_wake_turn
from src.wake_watcher import WakeWatcherConfig, run_wake_watcher
from src.worker_manager import WorkerManager
from chat.dashboard import build_routes as build_dashboard_routes
from chat.dispatch import dispatch
from chat.tool_definitions import TOOLS

logger = logging.getLogger(__name__)


def _wake_config_from_env() -> WakeWatcherConfig:
    return WakeWatcherConfig(
        interval_secs=float(os.environ.get("WAKE_WATCHER_INTERVAL_SECS", "1.0")),
        timeout_secs=float(os.environ.get("WAKE_SUBPROCESS_TIMEOUT_SECS", "300")),
        idle_expiry_secs=float(os.environ.get("WAKE_SESSION_IDLE_EXPIRY_SECS", "900")),
        max_failures=int(os.environ.get("WAKE_MAX_CONSECUTIVE_FAILURES", "3")),
        rate_limit_secs=float(
            os.environ.get("WAKE_ERROR_EMAIL_RATE_LIMIT_SECS", "3600"),
        ),
        claude_bin=os.environ.get("CLAUDE_BIN", "claude"),
        prompt=os.environ.get("WAKE_PROMPT", "Handle any pending bus messages."),
        user_avatar=os.environ.get("WAKE_USER_AVATAR_NAME", "user"),
    )


def create_app(db_path: str, host: str, port: int) -> Starlette:
    """Build a Starlette app with MCP SSE transport and tool handlers."""
    db = ChatDB(db_path)
    queue = TaskQueue(db_path)
    router_mcp_config = os.environ.get("ROUTER_MCP_CONFIG") or ROUTER_MCP_CONFIG_PATH
    manager = WorkerManager(
        db_path=db_path,
        project_root=os.environ.get("CLAUDE_CWD") or os.getcwd(),
        module_env={"ROUTER_MCP_CONFIG": router_mcp_config},
    )
    tokens = TokenStore()
    server = Server("claude-chat", version="1.0")
    sse = SseServerTransport("/messages/")

    @server.list_tools()
    async def handle_list_tools() -> list[Tool]:
        return TOOLS

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict) -> list[TextContent]:
        result = await dispatch(db, queue, manager, tokens, name, arguments)
        return [TextContent(type="text", text=json.dumps(result))]

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

    @contextlib.asynccontextmanager
    async def lifespan(app_):
        # Walk /proc on boot and refresh rows for any Claude CLI that's
        # already running — so a claude-chat restart doesn't leave the
        # dashboard blank until every session gets retriggered.
        try:
            reconcile_live_agents(db)
        except Exception:
            logger.exception("startup reconcile failed")
        stop = asyncio.Event()
        nudge = asyncio.Event()
        db.set_wake_nudge(nudge)
        cfg = _wake_config_from_env()
        task = asyncio.create_task(
            run_wake_watcher(db, cfg, stop, spawn_fn=run_wake_turn, nudge=nudge),
        )
        app_.state.wake_watcher_task = task
        app_.state.wake_watcher_stop = stop
        app_.state.wake_watcher_nudge = nudge
        app_.state.chat_db = db
        app_.state.worker_manager = manager
        try:
            yield
        finally:
            stop.set()
            nudge.set()  # unblock watcher sleep so shutdown is prompt
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=sse.handle_post_message),
            *build_dashboard_routes(),
        ],
        lifespan=lifespan,
    )
    app.state.mcp_server = server
    app.state.dashboard_poll_secs = float(
        os.environ.get("DASHBOARD_POLL_SECS", "1.0"),
    )
    return app
