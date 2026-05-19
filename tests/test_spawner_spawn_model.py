"""Tests for spawn_agent — path validation, basename collisions, and the
CLAUDE_MODEL / CLAUDE_EFFORT / CLAUDE_MAX_BUDGET_USD knobs."""
import pytest
from src.chat_db import ChatDB


class TestSpawnAgentValidation:
    @pytest.fixture
    def db(self, tmp_path):
        return ChatDB(str(tmp_path / "test.db"))

    def test_spawn_nonexistent_dir_raises(self, db, tmp_path, mocker):
        from src.spawner import spawn_agent

        mocker.patch("src.spawner.inject_mcp_config")

        with pytest.raises(ValueError, match="does not exist"):
            spawn_agent(db, str(tmp_path / "nope"), "http://localhost:8080/mcp")

    def test_spawn_outside_allowed_base_raises(self, db, tmp_path, mocker):
        from src.spawner import spawn_agent

        mocker.patch("src.spawner.inject_mcp_config")

        outside = tmp_path / "outside"
        outside.mkdir()
        base = tmp_path / "base"
        base.mkdir()

        with pytest.raises(ValueError, match="outside allowed base"):
            spawn_agent(
                db, str(outside), "http://localhost:8080/mcp",
                allowed_base=str(base),
            )

    def test_spawn_rejects_basename_collision_with_different_path(
        self, db, tmp_path, mocker,
    ):
        """build_agent_name collapses path→name as 'agent-' + basename, so
        /work/app and /backup/app both resolve to agent-app. If the first
        process died, the DB's ON CONFLICT DO UPDATE silently rewrites the
        project_path on the second spawn, and every downstream consumer
        that keyed on agent-app suddenly misroutes. Refuse the second
        spawn explicitly so the operator renames one of the dirs."""
        from src.spawner import spawn_agent

        mock_proc = mocker.MagicMock()
        mock_proc.pid = 999_998
        mocker.patch("src.spawner.subprocess.Popen", return_value=mock_proc)
        mocker.patch("src.spawner.inject_mcp_config")
        mocker.patch("src.spawner.inject_session_start_hook")
        mocker.patch("src.spawner.approve_mcp_server_for_project")

        work_app = tmp_path / "work" / "app"
        backup_app = tmp_path / "backup" / "app"
        work_app.mkdir(parents=True)
        backup_app.mkdir(parents=True)

        # First spawn — registers agent-app for /work/app
        name1, _ = spawn_agent(db, str(work_app), "http://chat")
        assert name1 == "agent-app"

        # Simulate that process dying (clear the pid so AgentNameTaken
        # won't fire on liveness; the bug is what happens *after* that).
        db.update_agent_pid(name1, None)

        # Second spawn in /backup/app — must refuse, not silently rewrite
        with pytest.raises(ValueError, match="agent-app"):
            spawn_agent(db, str(backup_app), "http://chat")

        # First spawn's slot must still point at /work/app
        row = db.get_agent("agent-app")
        assert row is not None
        assert row["project_path"] == str(work_app.resolve())


class TestSpawnAgentModelEffortBudget:
    """Tests for CLAUDE_MODEL, CLAUDE_EFFORT, CLAUDE_MAX_BUDGET_USD knobs in spawn_agent."""

    @pytest.fixture
    def db(self, tmp_path):
        return ChatDB(str(tmp_path / "test.db"))

    def _popen_mock(self, mocker, pid=77):
        mock_proc = mocker.MagicMock()
        mock_proc.pid = pid
        mock_popen = mocker.patch("src.spawner.subprocess.Popen", return_value=mock_proc)
        mocker.patch("src.spawner.inject_mcp_config")
        return mock_popen

    def test_model_flag_in_spawn_with_instruction(self, db, tmp_path, mocker):
        from src.spawner import spawn_agent
        mock_popen = self._popen_mock(mocker)
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        spawn_agent(db, str(project_dir), "http://localhost:8080/mcp",
                    instruction="go", model="claude-opus-4-5")
        cmd = mock_popen.call_args.args[0]
        assert "--model" in cmd
        assert cmd[cmd.index("--model") + 1] == "claude-opus-4-5"

    def test_model_flag_in_spawn_without_instruction(self, db, tmp_path, mocker):
        from src.spawner import spawn_agent
        mock_popen = self._popen_mock(mocker)
        project_dir = tmp_path / "proj2"
        project_dir.mkdir()
        spawn_agent(db, str(project_dir), "http://localhost:8080/mcp",
                    model="claude-opus-4-5")
        cmd = mock_popen.call_args.args[0]
        assert "--model" in cmd
        assert cmd[cmd.index("--model") + 1] == "claude-opus-4-5"

    def test_effort_flag_in_spawn_with_instruction(self, db, tmp_path, mocker):
        from src.spawner import spawn_agent
        mock_popen = self._popen_mock(mocker)
        project_dir = tmp_path / "proj3"
        project_dir.mkdir()
        spawn_agent(db, str(project_dir), "http://localhost:8080/mcp",
                    instruction="do it", effort="high")
        cmd = mock_popen.call_args.args[0]
        assert "--effort" in cmd
        assert cmd[cmd.index("--effort") + 1] == "high"

    def test_effort_flag_in_spawn_without_instruction(self, db, tmp_path, mocker):
        from src.spawner import spawn_agent
        mock_popen = self._popen_mock(mocker)
        project_dir = tmp_path / "proj4"
        project_dir.mkdir()
        spawn_agent(db, str(project_dir), "http://localhost:8080/mcp", effort="low")
        cmd = mock_popen.call_args.args[0]
        assert "--effort" in cmd
        assert cmd[cmd.index("--effort") + 1] == "low"

    def test_max_budget_usd_in_spawn_with_instruction(self, db, tmp_path, mocker):
        from src.spawner import spawn_agent
        mock_popen = self._popen_mock(mocker)
        project_dir = tmp_path / "proj5"
        project_dir.mkdir()
        spawn_agent(db, str(project_dir), "http://localhost:8080/mcp",
                    instruction="run", max_budget_usd="1.00")
        cmd = mock_popen.call_args.args[0]
        assert "--max-budget-usd" in cmd
        assert cmd[cmd.index("--max-budget-usd") + 1] == "1.00"

    def test_max_budget_usd_skipped_without_instruction_and_logs(self, db, tmp_path, mocker):
        from src.spawner import spawn_agent
        mock_popen = self._popen_mock(mocker)
        mock_logger = mocker.patch("src.spawner.logger")
        project_dir = tmp_path / "proj6"
        project_dir.mkdir()
        spawn_agent(db, str(project_dir), "http://localhost:8080/mcp",
                    max_budget_usd="1.00")
        cmd = mock_popen.call_args.args[0]
        assert "--max-budget-usd" not in cmd
        # should log exactly one INFO message about skipping
        info_calls = [c for c in mock_logger.info.call_args_list
                      if "budget" in c.args[0].lower()]
        assert len(info_calls) == 1
