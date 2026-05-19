"""Tests for agent spawner — name building and explicit agent_name kwarg."""
from src.chat_db import ChatDB
import pytest


class TestBuildAgentName:
    def test_build_agent_name(self):
        from src.spawner import build_agent_name
        assert build_agent_name("/home/user/0-projects/fits") == "agent-fits"

    def test_build_agent_name_trailing_slash(self):
        from src.spawner import build_agent_name
        assert build_agent_name("/home/user/0-projects/fits/") == "agent-fits"

    def test_build_agent_name_empty_path_falls_back(self):
        """Degenerate path → 'agent-unknown' so the bus identifier is
        never just 'agent-'. Lets task_notifier and status_envelope
        share this helper without re-implementing the fallback."""
        from src.spawner import build_agent_name
        assert build_agent_name("") == "agent-unknown"
        assert build_agent_name("/") == "agent-unknown"


class TestSpawnAgentName:
    """spawn_agent honors the agent_name kwarg and always injects CLAUDE_AGENT_NAME."""

    @pytest.fixture
    def db(self, tmp_path):
        return ChatDB(str(tmp_path / "test.db"))

    def test_agent_name_kwarg_overrides_basename(self, db, tmp_path, mocker):
        from src.spawner import spawn_agent

        mock_proc = mocker.MagicMock()
        mock_proc.pid = 12345
        mocker.patch("src.spawner.subprocess.Popen", return_value=mock_proc)
        mocker.patch("src.spawner.inject_mcp_config")

        project_dir = tmp_path / "shared-proj"
        project_dir.mkdir()
        name, pid = spawn_agent(
            db, str(project_dir), "http://chat",
            agent_name="agent-custom",
        )
        assert name == "agent-custom"
        # The DB row carries the explicit name, not agent-shared-proj.
        assert db.get_agent("agent-custom") is not None
        assert db.get_agent("agent-custom")["pid"] == 12345

    def test_env_var_injected_into_child(self, db, tmp_path, mocker):
        from src.spawner import spawn_agent

        mock_proc = mocker.MagicMock()
        mock_proc.pid = 12345
        mock_popen = mocker.patch("src.spawner.subprocess.Popen", return_value=mock_proc)
        mocker.patch("src.spawner.inject_mcp_config")

        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        spawn_agent(
            db, str(project_dir), "http://chat",
            agent_name="agent-custom",
        )
        env = mock_popen.call_args.kwargs["env"]
        assert env is not None
        assert env["CLAUDE_AGENT_NAME"] == "agent-custom"

    def test_env_var_injected_with_default_name(self, db, tmp_path, mocker):
        """Always set CLAUDE_AGENT_NAME so proc-scan can attribute reliably."""
        from src.spawner import spawn_agent

        mock_proc = mocker.MagicMock()
        mock_proc.pid = 12345
        mock_popen = mocker.patch("src.spawner.subprocess.Popen", return_value=mock_proc)
        mocker.patch("src.spawner.inject_mcp_config")

        project_dir = tmp_path / "auto-named"
        project_dir.mkdir()
        name, _ = spawn_agent(db, str(project_dir), "http://chat")
        env = mock_popen.call_args.kwargs["env"]
        assert env is not None
        assert env["CLAUDE_AGENT_NAME"] == name == "agent-auto-named"

    def test_invalid_agent_name_falls_back_to_default(
        self, db, tmp_path, mocker, capsys,
    ):
        from src.spawner import spawn_agent

        mock_proc = mocker.MagicMock()
        mock_proc.pid = 12345
        mocker.patch("src.spawner.subprocess.Popen", return_value=mock_proc)
        mocker.patch("src.spawner.inject_mcp_config")

        project_dir = tmp_path / "fall-back-target"
        project_dir.mkdir()
        name, _ = spawn_agent(
            db, str(project_dir), "http://chat",
            agent_name="Not Valid",
        )
        assert name == "agent-fall-back-target"
        assert "rejecting invalid name" in capsys.readouterr().err

    def test_explicit_agent_name_wins_over_extra_env_collision(
        self, db, tmp_path, mocker,
    ):
        """extra_env values must still flow to the child; CLAUDE_AGENT_NAME wins on collision."""
        from src.spawner import spawn_agent

        mock_proc = mocker.MagicMock()
        mock_proc.pid = 12345
        mock_popen = mocker.patch("src.spawner.subprocess.Popen", return_value=mock_proc)
        mocker.patch("src.spawner.inject_mcp_config")

        project_dir = tmp_path / "with-extra-env"
        project_dir.mkdir()
        spawn_agent(
            db, str(project_dir), "http://chat",
            extra_env={"FOO": "bar", "CLAUDE_AGENT_NAME": "agent-loser"},
            agent_name="agent-winner",
        )
        env = mock_popen.call_args.kwargs["env"]
        assert env["FOO"] == "bar"
        # Explicit agent_name overrides any CLAUDE_AGENT_NAME extra_env.
        assert env["CLAUDE_AGENT_NAME"] == "agent-winner"

    def test_explicit_agent_name_none_matches_omitted(self, db, tmp_path, mocker):
        """agent_name=None must behave identically to the kwarg being omitted —
        falls through validated_agent_name's empty-input branch silently."""
        from src.spawner import spawn_agent

        mock_proc = mocker.MagicMock()
        mock_proc.pid = 1
        mock_popen = mocker.patch("src.spawner.subprocess.Popen", return_value=mock_proc)
        mocker.patch("src.spawner.inject_mcp_config")

        project_dir = tmp_path / "explicit-none"
        project_dir.mkdir()
        name, _ = spawn_agent(
            db, str(project_dir), "http://chat", agent_name=None,
        )
        assert name == "agent-explicit-none"
        env = mock_popen.call_args.kwargs["env"]
        assert env["CLAUDE_AGENT_NAME"] == "agent-explicit-none"
