"""Tests for spawn_agent — basic subprocess invocation, args, env, hooks."""
import json
import os
import pytest
from src.chat_db import ChatDB


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

        project_dir = tmp_path / "my-project"
        project_dir.mkdir()
        name, pid = spawn_agent(db, str(project_dir), "http://localhost:8080/mcp")

        assert name == "agent-my-project"
        assert pid == 42

        mock_popen.assert_called_once()
        call_kwargs = mock_popen.call_args
        assert call_kwargs.kwargs["cwd"] == str(project_dir)
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

        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        name, pid = spawn_agent(
            db, str(project_dir), "http://localhost:8080/mcp",
            instruction="run all tests",
        )

        cmd = mock_popen.call_args.args[0]
        assert cmd == ["claude", "--print", "run all tests"]

    def test_spawn_agent_without_instruction_uses_interactive(self, db, tmp_path, mocker):
        from src.spawner import spawn_agent

        mock_proc = mocker.MagicMock()
        mock_proc.pid = 50
        mock_popen = mocker.patch("src.spawner.subprocess.Popen", return_value=mock_proc)
        mocker.patch("src.spawner.inject_mcp_config")

        project_dir = tmp_path / "idle"
        project_dir.mkdir()
        spawn_agent(db, str(project_dir), "http://localhost:8080/mcp")

        cmd = mock_popen.call_args.args[0]
        assert cmd == ["claude"]
        assert "--print" not in cmd

    def test_spawn_agent_uses_devnull(self, db, tmp_path, mocker):
        from src.spawner import spawn_agent
        import subprocess

        mock_proc = mocker.MagicMock()
        mock_proc.pid = 7
        mock_popen = mocker.patch("src.spawner.subprocess.Popen", return_value=mock_proc)
        mocker.patch("src.spawner.inject_mcp_config")

        project_dir = tmp_path / "p"
        project_dir.mkdir()
        spawn_agent(db, str(project_dir), "http://localhost:8080/mcp")

        kwargs = mock_popen.call_args.kwargs
        assert kwargs["stdout"] == subprocess.DEVNULL
        assert kwargs["stderr"] == subprocess.DEVNULL
        assert kwargs["stdin"] == subprocess.DEVNULL

    def test_spawn_agent_yolo_adds_skip_permissions(self, db, tmp_path, mocker):
        from src.spawner import spawn_agent

        mock_proc = mocker.MagicMock()
        mock_proc.pid = 11
        mock_popen = mocker.patch("src.spawner.subprocess.Popen", return_value=mock_proc)
        mocker.patch("src.spawner.inject_mcp_config")

        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        spawn_agent(
            db, str(project_dir), "http://localhost:8080/mcp",
            instruction="go", yolo=True,
        )
        cmd = mock_popen.call_args.args[0]
        assert cmd == ["claude", "--dangerously-skip-permissions", "--print", "go"]

    def test_spawn_agent_yolo_without_instruction(self, db, tmp_path, mocker):
        from src.spawner import spawn_agent

        mock_proc = mocker.MagicMock()
        mock_proc.pid = 12
        mock_popen = mocker.patch("src.spawner.subprocess.Popen", return_value=mock_proc)
        mocker.patch("src.spawner.inject_mcp_config")

        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        spawn_agent(db, str(project_dir), "http://localhost:8080/mcp", yolo=True)
        cmd = mock_popen.call_args.args[0]
        assert cmd == ["claude", "--dangerously-skip-permissions"]

    def test_spawn_agent_writes_session_start_hook(self, db, tmp_path, mocker):
        from src.spawner import spawn_agent

        mock_proc = mocker.MagicMock()
        mock_proc.pid = 101
        mocker.patch("src.spawner.subprocess.Popen", return_value=mock_proc)

        project_dir = tmp_path / "my-project"
        project_dir.mkdir()

        spawn_agent(db, str(project_dir), "http://localhost:8080/mcp")

        assert (project_dir / ".mcp.json").exists()
        settings = json.loads((project_dir / ".claude" / "settings.json").read_text())
        cmd = settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        assert os.path.isabs(cmd)
        assert cmd.endswith("/scripts/chat-session-start-hook.sh")

    def test_spawn_agent_extra_env(self, db, tmp_path, mocker):
        from src.spawner import spawn_agent

        mock_proc = mocker.MagicMock()
        mock_proc.pid = 13
        mock_popen = mocker.patch("src.spawner.subprocess.Popen", return_value=mock_proc)
        mocker.patch("src.spawner.inject_mcp_config")

        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        spawn_agent(
            db, str(project_dir), "http://localhost:8080/mcp",
            extra_env={"CLAUDE_CONFIG_DIR": "/home/u/.claude-personal", "IS_SANDBOX": "1"},
        )
        env = mock_popen.call_args.kwargs["env"]
        assert env["CLAUDE_CONFIG_DIR"] == "/home/u/.claude-personal"
        assert env["IS_SANDBOX"] == "1"
        assert "PATH" in env
