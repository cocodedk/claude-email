"""Tests for the MCP SSE server (chat/server.py)."""
import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient


@pytest.fixture
def app(tmp_path):
    from chat.server import create_app
    return create_app(str(tmp_path / "test.db"), "127.0.0.1", 8420)


class TestCreateApp:
    def test_returns_starlette_instance(self, app):
        assert isinstance(app, Starlette)

    def test_has_sse_route(self, app):
        paths = [r.path for r in app.routes]
        assert "/sse" in paths

    def test_has_messages_route(self, app):
        # Starlette's Mount normalizes "/messages/" to "/messages" in .path
        paths = [r.path for r in app.routes]
        assert "/messages" in paths

    def test_sse_route_is_get(self, app):
        for route in app.routes:
            if getattr(route, "path", None) == "/sse":
                # Starlette Route stores methods as a set of uppercase strings
                assert "GET" in route.methods
                break
        else:
            pytest.fail("/sse route not found")

    def test_messages_route_is_mount(self, app):
        """/messages/ is mounted as an ASGI app (MCP SDK's handle_post_message)."""
        from starlette.routing import Mount
        for route in app.routes:
            if getattr(route, "path", None) == "/messages":
                assert isinstance(route, Mount)
                assert route.app is not None
                break
        else:
            pytest.fail("/messages mount not found")


class TestToolRegistration:
    """Verify tools are registered on the MCP server."""

    def test_server_lists_expected_tools(self, app):
        """The app should carry a reference to the MCP server with tools cached."""
        from chat.server import create_app
        # We access the server through the app's state
        server = app.state.mcp_server
        # After list_tools handler is registered, the tool cache should
        # be populated on first call. We trigger it by listing.
        import asyncio

        async def _list():
            from mcp.types import ListToolsRequest
            handler = server.request_handlers[ListToolsRequest]
            result = await handler(ListToolsRequest(method="tools/list"))
            return result.root.tools

        tools = asyncio.run(_list())
        tool_names = {t.name for t in tools}
        expected = {
            "chat_register",
            "chat_ask",
            "chat_notify",
            "chat_message_agent",
            "chat_check_messages",
            "chat_list_agents",
            "chat_deregister",
            "chat_spawn_agent",
            "chat_enqueue_task",
            "chat_cancel_task",
            "chat_queue_status",
            "chat_reset_project",
            "chat_confirm_reset",
            "chat_where_am_i",
            "chat_commit_project",
            "chat_retry_task",
        }
        assert tool_names == expected

    def test_chat_register_schema(self, app):
        """chat_register should require name and project_path."""
        import asyncio
        from mcp.types import ListToolsRequest

        async def _list():
            server = app.state.mcp_server
            handler = server.request_handlers[ListToolsRequest]
            result = await handler(ListToolsRequest(method="tools/list"))
            return result.root.tools

        tools = asyncio.run(_list())
        reg = [t for t in tools if t.name == "chat_register"][0]
        assert set(reg.inputSchema["required"]) == {"name", "project_path"}

    def test_caller_tools_require_caller_param(self, app):
        """Tools that need identity should require _caller."""
        import asyncio
        from mcp.types import ListToolsRequest

        async def _list():
            server = app.state.mcp_server
            handler = server.request_handlers[ListToolsRequest]
            result = await handler(ListToolsRequest(method="tools/list"))
            return result.root.tools

        tools = asyncio.run(_list())
        caller_tools = {"chat_ask", "chat_notify", "chat_message_agent", "chat_check_messages", "chat_deregister"}
        for t in tools:
            if t.name in caller_tools:
                assert "_caller" in t.inputSchema.get("required", []), (
                    f"{t.name} should require _caller"
                )
