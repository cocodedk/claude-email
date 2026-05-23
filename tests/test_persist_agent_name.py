"""Tests for persisting agent name to .claude/agent-name."""
import json

import pytest

from src.chat_db import ChatDB


class TestPersistAgentName:
    def test_writes_agent_name_file(self, tmp_path):
        from src.agent_bootstrap import persist_agent_name
        persist_agent_name(str(tmp_path), "agent-em-backend")
        content = (tmp_path / ".claude" / "agent-name").read_text()
        assert content.strip() == "agent-em-backend"

    def test_creates_claude_dir_if_missing(self, tmp_path):
        from src.agent_bootstrap import persist_agent_name
        persist_agent_name(str(tmp_path), "agent-foo")
        assert (tmp_path / ".claude" / "agent-name").exists()

    def test_overwrites_existing_name(self, tmp_path):
        from src.agent_bootstrap import persist_agent_name
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "agent-name").write_text("agent-old\n")
        persist_agent_name(str(tmp_path), "agent-new")
        assert (tmp_path / ".claude" / "agent-name").read_text().strip() == "agent-new"

    def test_is_idempotent(self, tmp_path):
        from src.agent_bootstrap import persist_agent_name
        persist_agent_name(str(tmp_path), "agent-same")
        persist_agent_name(str(tmp_path), "agent-same")
        assert (tmp_path / ".claude" / "agent-name").read_text().strip() == "agent-same"


class TestSpawnWritesAgentName:
    @pytest.fixture
    def db(self, tmp_path):
        return ChatDB(str(tmp_path / "test.db"))

    def test_spawn_persists_agent_name(self, db, tmp_path, mocker):
        from src.spawner import spawn_agent

        mock_proc = mocker.MagicMock()
        mock_proc.pid = 42
        mocker.patch("src.spawner.subprocess.Popen", return_value=mock_proc)
        mocker.patch("src.spawner.inject_mcp_config")

        project = tmp_path / "earn-money-backend"
        project.mkdir()
        spawn_agent(
            db, str(project), "http://chat",
            agent_name="agent-em-backend",
        )
        content = (project / ".claude" / "agent-name").read_text()
        assert content.strip() == "agent-em-backend"

    def test_spawn_persists_default_name(self, db, tmp_path, mocker):
        from src.spawner import spawn_agent

        mock_proc = mocker.MagicMock()
        mock_proc.pid = 42
        mocker.patch("src.spawner.subprocess.Popen", return_value=mock_proc)
        mocker.patch("src.spawner.inject_mcp_config")

        project = tmp_path / "my-proj"
        project.mkdir()
        spawn_agent(db, str(project), "http://chat")
        content = (project / ".claude" / "agent-name").read_text()
        assert content.strip() == "agent-my-proj"
