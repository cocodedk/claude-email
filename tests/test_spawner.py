"""Tests for agent spawner — name building, MCP injection, process spawning."""
import json
import subprocess
import pytest
from src.chat_db import ChatDB


class TestBuildAgentName:
    def test_build_agent_name(self):
        from src.spawner import build_agent_name
        assert build_agent_name("/home/user/0-projects/fits") == "agent-fits"

    def test_build_agent_name_trailing_slash(self):
        from src.spawner import build_agent_name
        assert build_agent_name("/home/user/0-projects/fits/") == "agent-fits"


class TestInjectMcpConfig:
    def test_inject_mcp_config_creates_file(self, tmp_path):
        from src.spawner import inject_mcp_config

        project_dir = str(tmp_path)
        inject_mcp_config(project_dir, "http://localhost:8080/mcp")

        mcp_file = tmp_path / ".mcp.json"
        assert mcp_file.exists()
        data = json.loads(mcp_file.read_text())
        assert data == {
            "mcpServers": {
                "claude-chat": {"url": "http://localhost:8080/mcp"}
            }
        }

    def test_inject_mcp_config_merges_existing(self, tmp_path):
        from src.spawner import inject_mcp_config

        mcp_file = tmp_path / ".mcp.json"
        existing = {
            "mcpServers": {
                "playwright": {
                    "command": "npx",
                    "args": ["@playwright/mcp@latest"],
                }
            }
        }
        mcp_file.write_text(json.dumps(existing))

        inject_mcp_config(str(tmp_path), "http://localhost:9090/mcp")

        data = json.loads(mcp_file.read_text())
        # Existing server preserved
        assert data["mcpServers"]["playwright"] == {
            "command": "npx",
            "args": ["@playwright/mcp@latest"],
        }
        # New server added
        assert data["mcpServers"]["claude-chat"] == {
            "url": "http://localhost:9090/mcp"
        }


class TestSpawnAgent:
    @pytest.fixture
    def db(self, tmp_path):
        return ChatDB(str(tmp_path / "test.db"))

    def test_spawn_agent_calls_subprocess(self, db, tmp_path, mocker):
        from src.spawner import spawn_agent

        mock_proc = mocker.MagicMock()
        mock_proc.pid = 42
        mock_popen = mocker.patch("src.spawner.subprocess.Popen", return_value=mock_proc)
        mocker.patch("src.spawner.inject_mcp_config")

        project_dir = str(tmp_path / "my-project")
        name, pid = spawn_agent(db, project_dir, "http://localhost:8080/mcp")

        assert name == "agent-my-project"
        assert pid == 42

        mock_popen.assert_called_once()
        call_kwargs = mock_popen.call_args
        assert call_kwargs.kwargs["cwd"] == project_dir
        assert call_kwargs.kwargs["shell"] is False

        # DB was updated
        agent = db.get_agent("agent-my-project")
        assert agent is not None
        assert agent["pid"] == 42

    def test_spawn_agent_with_instruction(self, db, tmp_path, mocker):
        from src.spawner import spawn_agent

        mock_proc = mocker.MagicMock()
        mock_proc.pid = 99
        mock_popen = mocker.patch("src.spawner.subprocess.Popen", return_value=mock_proc)
        mocker.patch("src.spawner.inject_mcp_config")

        project_dir = str(tmp_path / "proj")
        name, pid = spawn_agent(
            db, project_dir, "http://localhost:8080/mcp",
            instruction="run all tests",
        )

        cmd = mock_popen.call_args.args[0]
        assert "run all tests" in cmd
        assert cmd[0] == "claude"
        assert "--print" in cmd
