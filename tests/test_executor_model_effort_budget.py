"""Tests for CLAUDE_MODEL, CLAUDE_EFFORT, CLAUDE_MAX_BUDGET_USD knobs."""
import subprocess
from src.executor import execute_command


class TestExecuteCommandModelEffortBudget:
    """Tests for CLAUDE_MODEL, CLAUDE_EFFORT, CLAUDE_MAX_BUDGET_USD knobs."""

    def _ok(self, mocker):
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="ok", stderr="",
        )
        return mock_run

    def test_model_flag_appended_when_set(self, mocker):
        mock_run = self._ok(mocker)
        execute_command("hello", model="claude-opus-4-5")
        cmd = mock_run.call_args.args[0]
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "claude-opus-4-5"

    def test_model_flag_absent_when_not_set(self, mocker):
        mock_run = self._ok(mocker)
        execute_command("hello")
        cmd = mock_run.call_args.args[0]
        assert "--model" not in cmd

    def test_effort_flag_appended_when_set(self, mocker):
        mock_run = self._ok(mocker)
        execute_command("hello", effort="high")
        cmd = mock_run.call_args.args[0]
        assert "--effort" in cmd
        idx = cmd.index("--effort")
        assert cmd[idx + 1] == "high"

    def test_effort_flag_absent_when_not_set(self, mocker):
        mock_run = self._ok(mocker)
        execute_command("hello")
        cmd = mock_run.call_args.args[0]
        assert "--effort" not in cmd

    def test_max_budget_usd_appended_for_execute_command(self, mocker):
        mock_run = self._ok(mocker)
        execute_command("hello", max_budget_usd="2.50")
        cmd = mock_run.call_args.args[0]
        assert "--max-budget-usd" in cmd
        idx = cmd.index("--max-budget-usd")
        assert cmd[idx + 1] == "2.50"

    def test_max_budget_usd_absent_when_not_set(self, mocker):
        mock_run = self._ok(mocker)
        execute_command("hello")
        cmd = mock_run.call_args.args[0]
        assert "--max-budget-usd" not in cmd

    def test_all_three_flags_together(self, mocker):
        mock_run = self._ok(mocker)
        execute_command("hello", model="claude-sonnet-4-5", effort="low", max_budget_usd="1.00")
        cmd = mock_run.call_args.args[0]
        assert "--model" in cmd
        assert "--effort" in cmd
        assert "--max-budget-usd" in cmd
