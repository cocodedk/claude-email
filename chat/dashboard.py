"""Dashboard HTTP routes for claude-chat — agents, messages, SSE stream.

Mounted onto the Starlette app in chat/server.py. Reads from the shared
ChatDB (app.state.chat_db) via DashboardQueriesMixin — no writes.

The /events stream is a simple polling loop on the messages watermark.
A browser tab is allowed to lag by up to `dashboard_poll_secs` before it
sees a new message; that's acceptable for a monitoring view.
"""
import asyncio
import json

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, StreamingResponse
from starlette.routing import Route

from chat.dashboard_page import DASHBOARD_HTML

MAX_MESSAGES_LIMIT = 500
DEFAULT_MESSAGES_LIMIT = 100


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


async def _dashboard(request: Request) -> HTMLResponse:
    return HTMLResponse(DASHBOARD_HTML)


async def _agents(request: Request) -> JSONResponse:
    db = request.app.state.chat_db
    return JSONResponse({"agents": db.get_agents_summary()})


async def _messages(request: Request) -> JSONResponse:
    db = request.app.state.chat_db
    raw = request.query_params.get("limit", str(DEFAULT_MESSAGES_LIMIT))
    try:
        limit = int(raw)
    except ValueError:
        limit = DEFAULT_MESSAGES_LIMIT
    limit = max(1, min(limit, MAX_MESSAGES_LIMIT))
    return JSONResponse({"messages": db.get_messages_summary(limit=limit)})


async def stream_events(db, is_disconnected, poll: float):
    """Async generator that yields SSE frames until the client disconnects.

    Extracted so tests can drive it with a synthetic `is_disconnected`
    without booting the whole Starlette app.

    Ships two kinds of frames:
      - kind:"message" — a new row in the messages table (drives the radar)
      - kind:"event"   — a flow-event (drives the technical-flow panel)
    """
    last_msg = db.latest_message_id()
    last_flow = db.latest_flow_event_id()
    yield _sse({"kind": "hello", "last_id": last_msg, "last_flow_id": last_flow})
    while True:
        if await is_disconnected():
            return
        for row in db.get_messages_since(last_msg):
            last_msg = row["id"]
            yield _sse({"kind": "message", **row})
        for row in db.get_flow_events_since(last_flow):
            last_flow = row["id"]
            yield _sse({"kind": "event", **row})
        yield ": keepalive\n\n"
        await asyncio.sleep(poll)


async def _events(request: Request) -> StreamingResponse:
    db = request.app.state.chat_db
    poll = float(getattr(request.app.state, "dashboard_poll_secs", 1.0))
    return StreamingResponse(
        stream_events(db, request.is_disconnected, poll),
        media_type="text/event-stream",
    )


def build_routes() -> list[Route]:
    return [
        Route("/dashboard", _dashboard, methods=["GET"]),
        Route("/api/agents", _agents, methods=["GET"]),
        Route("/api/messages", _messages, methods=["GET"]),
        Route("/events", _events, methods=["GET"]),
    ]
