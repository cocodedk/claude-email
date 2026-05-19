"""Integration tests for the main orchestration loop — basic dispatch & ack."""
import email.message
import pytest
from unittest.mock import patch

from tests._main_helpers import _make_authorized_msg, _make_unauthorized_msg


class TestOrchestration:
    def test_authorized_email_triggers_execution(self, mocker):
        from main import process_email
        mock_execute = mocker.patch("main.execute_command", return_value="file list output")
        mock_reply = mocker.patch("main.send_threaded_reply")

        msg = _make_authorized_msg("testsecret")
        config = {
            "authorized_sender": "user@example.com",
            "shared_secret": "testsecret",
            "gpg_fingerprint": "",
            "gpg_home": None,
            "smtp_host": "send.one.com",
            "smtp_port": 465,
            "username": "agent@example.com",
            "password": "pw",
            "claude_timeout": 30,
            "claude_bin": "claude",
        }
        process_email(msg, config)
        mock_execute.assert_called_once()
        # Two replies: progress ack, then final output
        assert mock_reply.call_count == 2

    def test_authorized_email_sends_progress_ack_before_execute(self, mocker):
        """The progress ack must be sent before execute_command runs, not after."""
        from main import process_email
        call_order = []
        mocker.patch(
            "main.execute_command",
            side_effect=lambda *a, **kw: (call_order.append("execute"), "output")[1],
        )
        mocker.patch(
            "main.send_threaded_reply",
            side_effect=lambda *a, **kw: call_order.append(f"reply:{a[2][:80]}"),
        )

        msg = _make_authorized_msg("testsecret")
        config = {
            "authorized_sender": "user@example.com",
            "shared_secret": "testsecret",
            "gpg_fingerprint": "", "gpg_home": None,
            "smtp_host": "h", "smtp_port": 465,
            "username": "u", "password": "pw",
            "claude_timeout": 42, "claude_bin": "claude",
        }
        process_email(msg, config)

        assert call_order[0].startswith("reply:")
        assert "Command received" in call_order[0]
        assert "42" in call_order[0]  # timeout should appear in the ack
        assert call_order[1] == "execute"
        assert call_order[2].startswith("reply:")

    def test_llm_router_enabled_passes_system_prompt_and_mcp_config(self, mocker):
        from main import process_email
        from src.llm_router import build_email_router_prompt
        from src.universes import Universe
        mock_execute = mocker.patch("main.execute_command", return_value="out")
        mocker.patch("main.send_threaded_reply")

        msg = _make_authorized_msg("testsecret")
        universe = Universe(
            sender="user@example.com",
            allowed_base="/home/u/proj",
            chat_db_path="claude-chat.db",
            chat_url="http://localhost:8420/sse",
            mcp_config="/repo/.mcp.json",
            service_name_chat="claude-chat.service",
        )
        config = {
            "authorized_sender": "user@example.com", "shared_secret": "testsecret",
            "gpg_fingerprint": "", "gpg_home": None,
            "smtp_host": "h", "smtp_port": 465, "username": "u", "password": "p",
            "claude_timeout": 10, "claude_bin": "claude",
            "llm_router": True,
            "_universe": universe,
        }
        process_email(msg, config)
        # The prompt is now sender-aware; without reply_to set on config it
        # falls back to the canonical sender.
        assert mock_execute.call_args.kwargs["system_prompt"] == build_email_router_prompt(
            reply_to="user@example.com",
        )
        assert mock_execute.call_args.kwargs["mcp_config"] == "/repo/.mcp.json"
        assert mock_execute.call_args.kwargs["cwd"] == "/home/u/proj"

    def test_llm_router_disabled_omits_system_prompt(self, mocker):
        from main import process_email
        mock_execute = mocker.patch("main.execute_command", return_value="out")
        mocker.patch("main.send_threaded_reply")

        msg = _make_authorized_msg("testsecret")
        config = {
            "authorized_sender": "user@example.com", "shared_secret": "testsecret",
            "gpg_fingerprint": "", "gpg_home": None,
            "smtp_host": "h", "smtp_port": 465, "username": "u", "password": "p",
            "claude_timeout": 10, "claude_bin": "claude",
        }
        process_email(msg, config)
        assert mock_execute.call_args.kwargs["system_prompt"] is None
        assert mock_execute.call_args.kwargs["mcp_config"] is None

    def test_progress_ack_failure_does_not_abort_execution(self, mocker):
        """If the ack send fails, we should still run the command."""
        import smtplib
        from main import process_email
        mock_execute = mocker.patch("main.execute_command", return_value="output")
        mock_reply = mocker.patch(
            "main.send_threaded_reply",
            side_effect=[smtplib.SMTPException("ack failed"), None],
        )

        msg = _make_authorized_msg("testsecret")
        config = {
            "authorized_sender": "user@example.com",
            "shared_secret": "testsecret",
            "gpg_fingerprint": "", "gpg_home": None,
            "smtp_host": "h", "smtp_port": 465,
            "username": "u", "password": "pw",
            "claude_timeout": 30, "claude_bin": "claude",
        }
        process_email(msg, config)

        # Execute ran despite ack failure
        mock_execute.assert_called_once()
        # Both sends attempted
        assert mock_reply.call_count == 2
