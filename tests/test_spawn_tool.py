"""Tests for spawn_agent_tool — MCP wrapper around src.spawner.spawn_agent."""
import pytest
from src.chat_db import ChatDB
from chat.tools import spawn_agent_tool


@pytest.fixture
def db(tmp_path):
    return ChatDB(str(tmp_path / "test.db"))


@pytest.fixture
def _mock_spawn_deps(mocker):
    """Patch out filesystem/subprocess side effects of src.spawner.spawn_agent."""
    mocker.patch("src.spawner.inject_mcp_config")
    mocker.patch("src.spawner.inject_session_start_hook")
    mocker.patch("src.spawner.approve_mcp_server_for_project")
    proc = mocker.MagicMock()
    proc.pid = 12345
    popen = mocker.patch("src.spawner.subprocess.Popen", return_value=proc)
    return popen


class TestSpawnAgentTool:
    def test_happy_path_returns_spawned_status(self, db, tmp_path, _mock_spawn_deps):
        (tmp_path / "myproj").mkdir()
        result = spawn_agent_tool(
            db, project=str(tmp_path / "myproj"), instruction="make tests",
            chat_url="http://localhost:8080/mcp",
            claude_bin="claude",
            allowed_base=str(tmp_path),
        )
        assert result == {"status": "spawned", "name": "agent-myproj", "pid": 12345}

    def test_resolves_bare_name_against_allowed_base(self, db, tmp_path, _mock_spawn_deps):
        (tmp_path / "fits").mkdir()
        result = spawn_agent_tool(
            db, project="fits", instruction="",
            chat_url="u", claude_bin="claude",
            allowed_base=str(tmp_path),
        )
        assert result["status"] == "spawned"
        assert result["name"] == "agent-fits"

    def test_nonexistent_path_returns_error(self, db, tmp_path):
        result = spawn_agent_tool(
            db, project="/does/not/exist/anywhere",
            instruction="x",
            chat_url="u", claude_bin="claude",
            allowed_base=str(tmp_path),
        )
        assert "error" in result
        assert "status" not in result

    def test_path_outside_allowed_base_returns_error(self, db, tmp_path):
        outside = tmp_path.parent / "outside-base"
        outside.mkdir(exist_ok=True)
        try:
            result = spawn_agent_tool(
                db, project=str(outside),
                instruction="x",
                chat_url="u", claude_bin="claude",
                allowed_base=str(tmp_path),
            )
            assert "error" in result
        finally:
            outside.rmdir()

    def test_passes_model_effort_budget_to_cli(self, db, tmp_path, _mock_spawn_deps):
        (tmp_path / "p").mkdir()
        spawn_agent_tool(
            db, project=str(tmp_path / "p"), instruction="x",
            chat_url="u", claude_bin="claude",
            allowed_base=str(tmp_path),
            model="claude-opus-4-7", effort="low",
            max_budget_usd="0.50",
        )
        cmd = _mock_spawn_deps.call_args[0][0]
        assert "--model" in cmd and "claude-opus-4-7" in cmd
        assert "--effort" in cmd and "low" in cmd
        assert "--max-budget-usd" in cmd and "0.50" in cmd

    def test_yolo_flag_propagates(self, db, tmp_path, _mock_spawn_deps):
        (tmp_path / "p").mkdir()
        spawn_agent_tool(
            db, project=str(tmp_path / "p"), instruction="",
            chat_url="u", claude_bin="claude",
            allowed_base=str(tmp_path),
            yolo=True,
        )
        cmd = _mock_spawn_deps.call_args[0][0]
        assert "--dangerously-skip-permissions" in cmd

    def test_empty_allowed_base_is_refused(self, db):
        result = spawn_agent_tool(
            db, project="anything",
            chat_url="u", claude_bin="claude",
            allowed_base="",
        )
        assert result == {"error": "CLAUDE_CWD not configured on chat server"}
