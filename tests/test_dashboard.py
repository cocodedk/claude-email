"""Tests for the live dashboard — HTTP routes and SSE stream."""
import asyncio
import json

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from chat.dashboard import (
    DEFAULT_MESSAGES_LIMIT,
    MAX_MESSAGES_LIMIT,
    build_routes,
    stream_events,
)
from chat.dashboard_page import DASHBOARD_HTML
from src.chat_db import ChatDB


@pytest.fixture
def db(tmp_path):
    return ChatDB(str(tmp_path / "test.db"))


@pytest.fixture
def client(db):
    app = Starlette(routes=build_routes())
    app.state.chat_db = db
    app.state.dashboard_poll_secs = 0.01
    return TestClient(app)


class TestBuildRoutes:
    def test_returns_four_get_routes(self):
        paths = {r.path for r in build_routes()}
        assert paths == {"/dashboard", "/api/agents", "/api/messages", "/events"}

    def test_all_are_get_only(self):
        for r in build_routes():
            assert r.methods == {"GET", "HEAD"}


class TestDashboardPage:
    def test_serves_inline_html(self, client):
        r = client.get("/dashboard")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")
        assert "CLAUDE.CHAT" in r.text
        assert "node graph" in r.text.lower()

    def test_html_constant_is_complete(self):
        assert DASHBOARD_HTML.lstrip().startswith("<!doctype html>")
        assert "EventSource('events')" in DASHBOARD_HTML
        assert "<svg" in DASHBOARD_HTML  # graphic, not a text list
        assert 'id="graph"' in DASHBOARD_HTML


class TestFlowPanel:
    """The /dashboard has a second face: a technical-flow illustration
    toggled from the topbar (observatory ↔ flow). It's pure SVG/CSS with
    no backend data — these checks confirm the markup is present and the
    two code paths (Stop-hook self-poll, wake_watcher spawn) are labelled."""

    def test_mode_toggle_buttons_present(self):
        assert 'id="modeObs"' in DASHBOARD_HTML
        assert 'id="modeFlow"' in DASHBOARD_HTML
        assert "observatory" in DASHBOARD_HTML
        # Default mode is observatory; flow button starts unpressed.
        assert 'id="modeObs" type="button" aria-pressed="true"' in DASHBOARD_HTML
        assert 'id="modeFlow" type="button" aria-pressed="false"' in DASHBOARD_HTML

    def test_flow_layer_embedded(self):
        assert 'id="flowLayer"' in DASHBOARD_HTML
        assert 'id="flow"' in DASHBOARD_HTML  # the inner flow <svg>
        assert "flow-title" in DASHBOARD_HTML

    def test_both_paths_labelled(self):
        assert "STOP-HOOK SELF-POLL" in DASHBOARD_HTML
        assert "SPAWN DORMANT AGENT" in DASHBOARD_HTML

    def test_stop_hook_lane_names_key_actors(self):
        # Every actor in the prose explainer must appear somewhere.
        for needle in (
            "chat_message_agent",
            "claude-chat.db",
            "Stop hook fires",
            "chat-drain-inbox.py",
            'decision: &quot;block&quot;',  # rendered by the f-string escape
        ):
            assert needle in DASHBOARD_HTML, f"missing: {needle}"

    def test_wake_lane_names_key_actors(self):
        for needle in (
            "wake_watcher",
            "SessionStart hook",
            "claude --print",
            "--resume &lt;session&gt;",
        ):
            assert needle in DASHBOARD_HTML, f"missing: {needle}"

    def test_mode_toggle_js_binds_localstorage(self):
        assert "bindModeToggle" in DASHBOARD_HTML
        assert "dashboard.mode" in DASHBOARD_HTML
        assert "show-flow" in DASHBOARD_HTML


class TestAgentsEndpoint:
    def test_empty(self, client):
        assert client.get("/api/agents").json() == {"agents": []}

    def test_lists_registered_agents(self, client, db):
        db.register_agent("alpha", "/p/a")
        db.register_agent("beta", "/p/b")
        data = client.get("/api/agents").json()
        names = {a["name"] for a in data["agents"]}
        assert names == {"alpha", "beta"}


class TestMessagesEndpoint:
    def test_empty(self, client):
        assert client.get("/api/messages").json() == {"messages": []}

    def test_returns_messages(self, client, db):
        db.insert_message("a", "b", "hello", "notify")
        data = client.get("/api/messages").json()
        assert len(data["messages"]) == 1
        assert data["messages"][0]["body"] == "hello"

    def test_limit_clamped_to_max(self, client, db):
        for i in range(3):
            db.insert_message("a", "b", f"m{i}", "notify")
        r = client.get(f"/api/messages?limit={MAX_MESSAGES_LIMIT + 5000}")
        assert len(r.json()["messages"]) == 3  # only 3 in DB, but didn't reject

    def test_limit_clamped_to_min(self, client, db):
        for i in range(3):
            db.insert_message("a", "b", f"m{i}", "notify")
        r = client.get("/api/messages?limit=0")
        assert len(r.json()["messages"]) == 1

    def test_invalid_limit_falls_back_to_default(self, client, db):
        for i in range(DEFAULT_MESSAGES_LIMIT + 2):
            db.insert_message("a", "b", f"m{i}", "notify")
        r = client.get("/api/messages?limit=notanumber")
        assert len(r.json()["messages"]) == DEFAULT_MESSAGES_LIMIT


class TestStreamEvents:
    """Drive the async generator directly with a synthetic is_disconnected."""

    def test_emits_hello_and_exits_when_disconnected(self, db):
        async def always_disconnected():
            return True

        async def run():
            return [c async for c in stream_events(db, always_disconnected, 0.001)]

        chunks = asyncio.run(run())
        assert len(chunks) == 1
        assert "hello" in chunks[0]
        assert "last_id" in chunks[0]

    def test_emits_new_messages_then_keepalive(self, db):
        calls = {"n": 0}

        async def disconnect_second_call():
            calls["n"] += 1
            return calls["n"] > 1

        async def run():
            gen = stream_events(db, disconnect_second_call, 0.001)
            # Pull hello — the watermark is now captured.
            hello_chunk = await gen.__anext__()
            # Insert a message AFTER the watermark so it appears in the stream.
            m = db.insert_message("alice", "bob", "heya", "notify")
            rest = [c async for c in gen]
            return hello_chunk, m, rest

        hello_chunk, m, rest = asyncio.run(run())
        hello = json.loads(hello_chunk[len("data: "):].strip())
        assert hello["kind"] == "hello"
        msg = json.loads(rest[0][len("data: "):].strip())
        assert msg["kind"] == "message"
        assert msg["from_name"] == "alice"
        assert msg["to_name"] == "bob"
        assert msg["body"] == "heya"
        assert msg["id"] == m["id"]
        assert rest[1].startswith(":")  # keepalive

    def test_hello_carries_current_watermark(self, db):
        db.insert_message("a", "b", "one", "notify")
        last = db.insert_message("a", "b", "two", "notify")

        async def immediately_disconnected():
            return True

        async def run():
            return [c async for c in stream_events(
                db, immediately_disconnected, 0.001,
            )]

        chunks = asyncio.run(run())
        hello = json.loads(chunks[0][len("data: "):].strip())
        assert hello["last_id"] == last["id"]

    def test_no_messages_yields_keepalive_only_per_tick(self, db):
        calls = {"n": 0}

        async def disconnect_after_one_tick():
            calls["n"] += 1
            return calls["n"] > 1

        async def run():
            return [c async for c in stream_events(
                db, disconnect_after_one_tick, 0.001,
            )]

        chunks = asyncio.run(run())
        # hello + keepalive (no messages to stream)
        assert len(chunks) == 2
        assert chunks[1].startswith(":")


class TestEventsHandler:
    """Unit-test the HTTP handler wrapping stream_events (lines 65-71)."""

    def test_returns_streaming_response_with_event_stream_mime(self, db):
        from types import SimpleNamespace
        from starlette.responses import StreamingResponse
        from chat.dashboard import _events

        app = SimpleNamespace(
            state=SimpleNamespace(chat_db=db, dashboard_poll_secs=0.001),
        )

        async def immediately_disconnected():
            return True

        request = SimpleNamespace(app=app, is_disconnected=immediately_disconnected)
        resp = asyncio.run(_events(request))
        assert isinstance(resp, StreamingResponse)
        assert resp.media_type == "text/event-stream"

    def test_falls_back_to_default_poll_when_state_missing(self, db):
        """If dashboard_poll_secs isn't set on app.state, use 1.0s default."""
        from types import SimpleNamespace
        from chat.dashboard import _events

        state = SimpleNamespace(chat_db=db)  # no dashboard_poll_secs
        app = SimpleNamespace(state=state)

        async def immediately_disconnected():
            return True

        request = SimpleNamespace(app=app, is_disconnected=immediately_disconnected)
        resp = asyncio.run(_events(request))
        assert resp.media_type == "text/event-stream"


class TestServerWiring:
    def test_dashboard_routes_mounted_on_server(self, tmp_path):
        from chat.server import create_app
        app = create_app(str(tmp_path / "t.db"), "127.0.0.1", 0)
        paths = {getattr(r, "path", None) for r in app.routes}
        assert "/dashboard" in paths
        assert "/api/agents" in paths
        assert "/api/messages" in paths
        assert "/events" in paths

    def test_dashboard_poll_secs_from_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DASHBOARD_POLL_SECS", "0.25")
        from chat.server import create_app
        app = create_app(str(tmp_path / "t.db"), "127.0.0.1", 0)
        assert app.state.dashboard_poll_secs == 0.25

    def test_dashboard_poll_secs_default(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DASHBOARD_POLL_SECS", raising=False)
        from chat.server import create_app
        app = create_app(str(tmp_path / "t.db"), "127.0.0.1", 0)
        assert app.state.dashboard_poll_secs == 1.0

    def test_dashboard_endpoint_on_full_server(self, tmp_path):
        from chat.server import create_app
        app = create_app(str(tmp_path / "t.db"), "127.0.0.1", 0)
        with TestClient(app) as c:
            r = c.get("/dashboard")
            assert r.status_code == 200
            assert "CLAUDE.CHAT" in r.text
            r = c.get("/api/agents")
            assert r.json() == {"agents": []}
