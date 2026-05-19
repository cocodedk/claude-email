"""Tests for the MCP SSE server (chat/server.py)."""
import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient


@pytest.fixture
def app(tmp_path):
    from chat.server import create_app
    return create_app(str(tmp_path / "test.db"), "127.0.0.1", 8420)


class TestToolDispatch:
    """Verify that call_tool dispatches correctly to chat.tools functions."""

    def test_call_chat_register(self, app):
        import asyncio
        from mcp.types import CallToolRequest, CallToolRequestParams

        async def _call():
            server = app.state.mcp_server
            handler = server.request_handlers[CallToolRequest]
            result = await handler(CallToolRequest(
                method="tools/call",
                params=CallToolRequestParams(
                    name="chat_register",
                    arguments={"name": "test-agent", "project_path": "/tmp/test"},
                ),
            ))
            return result

        result = asyncio.run(_call())
        # The result should be a ServerResult wrapping CallToolResult
        content = result.root.content
        assert len(content) == 1
        import json
        data = json.loads(content[0].text)
        assert data["status"] == "registered"
        assert data["name"] == "test-agent"

    def test_call_chat_list_agents(self, app):
        import asyncio
        import json
        from mcp.types import CallToolRequest, CallToolRequestParams

        async def _call():
            server = app.state.mcp_server
            handler = server.request_handlers[CallToolRequest]
            # First register
            await handler(CallToolRequest(
                method="tools/call",
                params=CallToolRequestParams(
                    name="chat_register",
                    arguments={"name": "a1", "project_path": "/p1"},
                ),
            ))
            # Then list
            result = await handler(CallToolRequest(
                method="tools/call",
                params=CallToolRequestParams(
                    name="chat_list_agents",
                    arguments={},
                ),
            ))
            return result

        result = asyncio.run(_call())
        data = json.loads(result.root.content[0].text)
        assert len(data["agents"]) == 1
        assert data["agents"][0]["name"] == "a1"

    def test_call_chat_notify(self, app):
        import asyncio
        import json
        from mcp.types import CallToolRequest, CallToolRequestParams

        async def _call():
            server = app.state.mcp_server
            handler = server.request_handlers[CallToolRequest]
            await handler(CallToolRequest(
                method="tools/call",
                params=CallToolRequestParams(
                    name="chat_register",
                    arguments={"name": "bot", "project_path": "/p"},
                ),
            ))
            result = await handler(CallToolRequest(
                method="tools/call",
                params=CallToolRequestParams(
                    name="chat_notify",
                    arguments={"message": "hello", "_caller": "bot"},
                ),
            ))
            return result

        result = asyncio.run(_call())
        data = json.loads(result.root.content[0].text)
        assert data["status"] == "sent"

    def test_call_chat_message_agent(self, app):
        import asyncio
        import json
        from mcp.types import CallToolRequest, CallToolRequestParams

        async def _call():
            server = app.state.mcp_server
            handler = server.request_handlers[CallToolRequest]
            for n in ("sender", "peer"):
                await handler(CallToolRequest(
                    method="tools/call",
                    params=CallToolRequestParams(
                        name="chat_register",
                        arguments={"name": n, "project_path": f"/p/{n}"},
                    ),
                ))
            return await handler(CallToolRequest(
                method="tools/call",
                params=CallToolRequestParams(
                    name="chat_message_agent",
                    arguments={
                        "_caller": "sender",
                        "to_agent": "peer",
                        "message": "heads up",
                    },
                ),
            ))

        result = asyncio.run(_call())
        data = json.loads(result.root.content[0].text)
        assert data == {"status": "sent", "to": "peer"}

    def test_call_chat_message_agent_unknown_recipient(self, app):
        import asyncio
        import json
        from mcp.types import CallToolRequest, CallToolRequestParams

        async def _call():
            server = app.state.mcp_server
            handler = server.request_handlers[CallToolRequest]
            await handler(CallToolRequest(
                method="tools/call",
                params=CallToolRequestParams(
                    name="chat_register",
                    arguments={"name": "sender", "project_path": "/p"},
                ),
            ))
            return await handler(CallToolRequest(
                method="tools/call",
                params=CallToolRequestParams(
                    name="chat_message_agent",
                    arguments={
                        "_caller": "sender",
                        "to_agent": "agent-typo",
                        "message": "hi",
                    },
                ),
            ))

        result = asyncio.run(_call())
        data = json.loads(result.root.content[0].text)
        assert "error" in data
        assert "agent-typo" in data["error"]

    def test_call_chat_deregister(self, app):
        import asyncio
        import json
        from mcp.types import CallToolRequest, CallToolRequestParams

        async def _call():
            server = app.state.mcp_server
            handler = server.request_handlers[CallToolRequest]
            await handler(CallToolRequest(
                method="tools/call",
                params=CallToolRequestParams(
                    name="chat_register",
                    arguments={"name": "bot", "project_path": "/p"},
                ),
            ))
            result = await handler(CallToolRequest(
                method="tools/call",
                params=CallToolRequestParams(
                    name="chat_deregister",
                    arguments={"_caller": "bot"},
                ),
            ))
            return result

        result = asyncio.run(_call())
        data = json.loads(result.root.content[0].text)
        assert data["status"] == "deregistered"
