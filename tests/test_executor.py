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
        )

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
