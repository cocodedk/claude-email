"""Tests for `execute_command` — subprocess invocation, flags, env, errors."""
import subprocess
import pytest
from src.executor import execute_command


class TestExecuteCommand:
    def test_successful_execution(self, mocker):
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess(
            args=["claude", "--print", "hello"],
            returncode=0,
            stdout="Hello world\n",
            stderr="",
        )
        result = execute_command("hello", claude_bin="claude", timeout=30)
        assert "Hello world" in result
        mock_run.assert_called_once_with(
            ["claude", "--print", "hello"],
            capture_output=True,
            text=True,
            timeout=30,
            shell=False,
            cwd=None,
            env=None,
        )

    def test_cwd_passed_to_subprocess(self, mocker):
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="ok", stderr="",
        )
        execute_command("hello", cwd="/home/user/projects")
        _, kwargs = mock_run.call_args
        assert kwargs["cwd"] == "/home/user/projects"

    def test_timeout_returns_error_message(self, mocker):
        mocker.patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=5))
        result = execute_command("hang forever", timeout=5)
        assert "timed out" in result.lower()

    def test_nonzero_exit_includes_stderr(self, mocker):
        mocker.patch("subprocess.run", return_value=subprocess.CompletedProcess(
            args=["claude", "--print", "bad"],
            returncode=1,
            stdout="",
            stderr="error: bad command",
        ))
        result = execute_command("bad")
        assert "error: bad command" in result

    def test_output_truncated_at_limit(self, mocker):
        big_output = "x" * 200_000
        mocker.patch("subprocess.run", return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout=big_output, stderr=""
        ))
        result = execute_command("big", max_output_bytes=50_000)
        assert len(result) <= 51_000  # some tolerance for truncation message
        assert "[truncated]" in result

    def test_file_not_found_returns_error(self, mocker):
        mocker.patch("subprocess.run", side_effect=FileNotFoundError())
        result = execute_command("hello", claude_bin="/nonexistent/claude")
        assert "[Error:" in result
        assert "not found" in result

    def test_generic_exception_returns_error(self, mocker):
        mocker.patch("subprocess.run", side_effect=OSError("permission denied"))
        result = execute_command("hello")
        assert "[Error:" in result
        assert "permission denied" in result

    def test_yolo_adds_skip_permissions_flag(self, mocker):
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="ok", stderr="",
        )
        execute_command("hello", yolo=True)
        cmd = mock_run.call_args.args[0]
        assert "--dangerously-skip-permissions" in cmd
        # Default behavior: no flag
        mock_run.reset_mock()
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="ok", stderr="",
        )
        execute_command("hello")
        cmd = mock_run.call_args.args[0]
        assert "--dangerously-skip-permissions" not in cmd

    def test_system_prompt_appends_flag(self, mocker):
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="ok", stderr="",
        )
        execute_command("hi", system_prompt="You are the email dispatcher.")
        cmd = mock_run.call_args.args[0]
        assert "--append-system-prompt" in cmd
        idx = cmd.index("--append-system-prompt")
        assert cmd[idx + 1] == "You are the email dispatcher."

    def test_system_prompt_absent_when_none(self, mocker):
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="ok", stderr="",
        )
        execute_command("hi")
        cmd = mock_run.call_args.args[0]
        assert "--append-system-prompt" not in cmd

    def test_mcp_config_adds_flag(self, mocker):
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="ok", stderr="",
        )
        execute_command("hi", mcp_config="/path/to/.mcp.json")
        cmd = mock_run.call_args.args[0]
        assert "--mcp-config" in cmd
        assert cmd[cmd.index("--mcp-config") + 1] == "/path/to/.mcp.json"

    def test_mcp_config_absent_when_none(self, mocker):
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="ok", stderr="",
        )
        execute_command("hi")
        cmd = mock_run.call_args.args[0]
        assert "--mcp-config" not in cmd

    def test_extra_env_merged_into_subprocess_env(self, mocker):
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="ok", stderr="",
        )
        execute_command(
            "hello",
            extra_env={"CLAUDE_CONFIG_DIR": "/home/u/.claude-personal", "IS_SANDBOX": "1"},
        )
        env = mock_run.call_args.kwargs["env"]
        assert env["CLAUDE_CONFIG_DIR"] == "/home/u/.claude-personal"
        assert env["IS_SANDBOX"] == "1"
        # Parent env still present
        assert "PATH" in env

    def test_no_extra_env_leaves_env_unset(self, mocker):
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="ok", stderr="",
        )
        execute_command("hello")
        # When extra_env is not provided, don't pass env= so child inherits parent
        assert mock_run.call_args.kwargs.get("env") is None
