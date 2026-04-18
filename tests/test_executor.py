"""Tests for command extraction and execution."""
import email.message
import subprocess
import pytest
from src.executor import extract_command, execute_command


def _text_msg(body: str, subject: str = "AUTH:secret cmd") -> email.message.Message:
    msg = email.message.EmailMessage()
    msg["Subject"] = subject
    msg.set_content(body)
    return msg


def _multipart_msg(text: str, html: str) -> email.message.Message:
    msg = email.message.EmailMessage()
    msg["Subject"] = "test"
    msg.set_content(text)
    msg.add_alternative(f"<html><body>{html}</body></html>", subtype="html")
    return msg


class TestExtractCommand:
    def test_simple_text_body(self):
        msg = _text_msg("list files in /tmp")
        assert extract_command(msg) == "list files in /tmp"

    def test_strips_quoted_reply(self):
        msg = _text_msg("do the thing\n\nOn Mon, Apr 14 2025 wrote:\n> old content")
        cmd = extract_command(msg)
        assert "do the thing" in cmd
        assert "> old content" not in cmd

    def test_multipart_uses_plain_text(self):
        msg = _multipart_msg("plain command", "<b>html command</b>")
        assert extract_command(msg) == "plain command"

    def test_strips_leading_trailing_whitespace(self):
        msg = _text_msg("  run tests  \n")
        assert extract_command(msg) == "run tests"

    def test_html_only_falls_back_gracefully(self):
        msg = email.message.EmailMessage()
        msg["Subject"] = "test"
        msg.add_alternative("<html><body><p>run tests</p></body></html>", subtype="html")
        result = extract_command(msg)
        assert isinstance(result, str)
        assert len(result) >= 0  # does not crash

    def test_strips_outlook_quote_block(self):
        """Outlook replies include a _____ separator + From:/Sent:/... header
        block + the full quoted message. All of that must be stripped so
        thread length doesn't balloon the CLI prompt or chat_db bodies.
        """
        msg = _text_msg(
            "Fix the bug please\n"
            "\n"
            "\n"
            "________________________________\n"
            "From: agent@example.com <agent@example.com>\n"
            "Sent: Saturday, April 18, 2026 5:52:14 PM\n"
            "To: Babak Bandpey <user@example.com>\n"
            "Subject: Re: [master-fixer] message\n"
            "\n"
            "This is the prior long email chain that shouldn't be in the "
            "command prompt — " + "x " * 200
        )
        result = extract_command(msg)
        assert result == "Fix the bug please"
        assert "From:" not in result
        assert "x x x" not in result

    def test_strips_original_message_separator(self):
        """Some clients use '----- Original Message -----' instead of Outlook's underscores."""
        msg = _text_msg(
            "My new reply\n"
            "\n"
            "----- Original Message -----\n"
            "From: someone@example.com\n"
            "the old message body"
        )
        result = extract_command(msg)
        assert result == "My new reply"

    def test_keeps_non_quote_underscores(self):
        """A normal paragraph with underscores must not be mistaken for an Outlook quote.

        Short rules of thumb matter: the underscore line must be long (>=20)
        AND immediately followed by 'From:' for it to count as a quote.
        """
        msg = _text_msg("my command __ with __ underscores __ in text")
        assert extract_command(msg) == "my command __ with __ underscores __ in text"


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


class TestExtractCommandHtmlOnly:
    def test_single_part_html(self):
        msg = email.message.EmailMessage()
        msg["Subject"] = "test"
        msg.set_content("<p>run tests</p>", subtype="html")
        result = extract_command(msg)
        assert "run tests" in result
