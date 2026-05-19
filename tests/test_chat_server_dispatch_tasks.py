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

    def test_call_chat_enqueue_task(self, app, tmp_path, mocker, monkeypatch):
        import asyncio
        import json
        from mcp.types import CallToolRequest, CallToolRequestParams

        monkeypatch.setenv("CLAUDE_CWD", str(tmp_path))
        (tmp_path / "p").mkdir()
        proc = mocker.MagicMock(pid=42)
        proc.poll.return_value = None
        mocker.patch("src.worker_manager.is_alive", return_value=True)
        mocker.patch("src.worker_manager._find_external_worker_pid", return_value=None)
        mocker.patch("src.worker_manager.subprocess.Popen", return_value=proc)

        async def _call():
            server = app.state.mcp_server
            handler = server.request_handlers[CallToolRequest]
            return await handler(CallToolRequest(
                method="tools/call",
                params=CallToolRequestParams(
                    name="chat_enqueue_task",
                    arguments={"project": "p", "body": "hello", "priority": 0},
                ),
            ))

        result = asyncio.run(_call())
        data = json.loads(result.root.content[0].text)
        assert data["status"] == "enqueued"
        assert data["worker_pid"] == 42
        assert "task_id" in data

    def test_call_chat_spawn_agent(self, app, tmp_path, mocker, monkeypatch):
        import asyncio
        import json
        from mcp.types import CallToolRequest, CallToolRequestParams

        monkeypatch.setenv("CLAUDE_CWD", str(tmp_path))
        monkeypatch.setenv("CHAT_URL", "http://localhost/mcp")
        monkeypatch.setenv("CLAUDE_BIN", "claude")
        (tmp_path / "newproj").mkdir()
        mocker.patch("src.spawner.inject_mcp_config")
        mocker.patch("src.spawner.inject_session_start_hook")
        mocker.patch("src.spawner.approve_mcp_server_for_project")
        proc = mocker.MagicMock()
        proc.pid = 777
        mocker.patch("src.spawner.subprocess.Popen", return_value=proc)

        async def _call():
            server = app.state.mcp_server
            handler = server.request_handlers[CallToolRequest]
            return await handler(CallToolRequest(
                method="tools/call",
                params=CallToolRequestParams(
                    name="chat_spawn_agent",
                    arguments={"project": "newproj", "instruction": "make tests"},
                ),
            ))

        result = asyncio.run(_call())
        data = json.loads(result.root.content[0].text)
        assert data == {"status": "spawned", "name": "agent-newproj", "pid": 777}
