"""Integration tests for the main orchestration loop — router / question short-circuit."""
import email.message
import pytest
from unittest.mock import MagicMock, patch, call

from tests._main_helpers import _make_authorized_msg, _make_unauthorized_msg


class TestOrchestration:
    def test_plain_question_short_circuits_router_no_running_ack(self, mocker):
        """Plain question + router on → one Answer reply, no task pipeline (cocodedk Task #40)."""
        from main import process_email
        mock_execute = mocker.patch(
            "src.question_handler.execute_command",
            return_value="origin/master @ bd69528",
        )
        mock_reply = mocker.patch("src.question_handler.send_threaded_reply")

        msg = email.message.EmailMessage()
        msg["From"] = "user@example.com"
        msg["Return-Path"] = "<user@example.com>"
        msg["Subject"] = "AUTH:testsecret q"
        msg["Message-ID"] = "<q001@mail>"
        msg.set_content("Which repo was it pushed to last time?")
        config = {
            "authorized_sender": "user@example.com", "shared_secret": "testsecret",
            "gpg_fingerprint": "", "gpg_home": None,
            "smtp_host": "h", "smtp_port": 465, "username": "u", "password": "p",
            "claude_timeout": 60, "claude_bin": "claude",
            "llm_router": True,
        }
        process_email(msg, config)
        mock_execute.assert_called_once()
        # Single reply, tagged "Answer". No Running ack, no Result tag.
        assert mock_reply.call_count == 1
        assert mock_reply.call_args.kwargs["tag"] == "Answer"
        assert mock_reply.call_args.kwargs["kind"] == "question_answer"

    def test_plain_question_uses_question_answer_prompt(self, mocker):
        """Short-circuit uses the dedicated question prompt (not the router prompt)."""
        from main import process_email
        from src.llm_router import build_question_answer_prompt
        mock_execute = mocker.patch(
            "src.question_handler.execute_command", return_value="answer",
        )
        mocker.patch("src.question_handler.send_threaded_reply")

        msg = email.message.EmailMessage()
        msg["From"] = "user@example.com"
        msg["Return-Path"] = "<user@example.com>"
        msg["Subject"] = "AUTH:testsecret q"
        msg["Message-ID"] = "<q002@mail>"
        msg.set_content("What's the latest commit on master?")
        config = {
            "authorized_sender": "user@example.com", "shared_secret": "testsecret",
            "gpg_fingerprint": "", "gpg_home": None,
            "smtp_host": "h", "smtp_port": 465, "username": "u", "password": "p",
            "claude_timeout": 60, "claude_bin": "claude",
            "llm_router": True,
        }
        process_email(msg, config)
        kwargs = mock_execute.call_args.kwargs
        assert kwargs["system_prompt"] == build_question_answer_prompt()

    def test_command_still_routes_to_email_router(self, mocker):
        """Real commands still route through the two-reply (Running + Result) path."""
        from main import process_email
        mocker.patch("main.execute_command", return_value="out")
        mock_reply = mocker.patch("main.send_threaded_reply")

        msg = email.message.EmailMessage()
        msg["From"] = "user@example.com"
        msg["Return-Path"] = "<user@example.com>"
        msg["Subject"] = "AUTH:testsecret cmd"
        msg["Message-ID"] = "<c001@mail>"
        msg.set_content("implement a feature flag for X")
        config = {
            "authorized_sender": "user@example.com", "shared_secret": "testsecret",
            "gpg_fingerprint": "", "gpg_home": None,
            "smtp_host": "h", "smtp_port": 465, "username": "u", "password": "p",
            "claude_timeout": 60, "claude_bin": "claude",
            "llm_router": True,
        }
        process_email(msg, config)
        assert mock_reply.call_count == 2
        tags = [c.kwargs["tag"] for c in mock_reply.call_args_list]
        assert tags == ["Running", "Result"]

    def test_question_short_circuit_only_when_router_enabled(self, mocker):
        """LLM_ROUTER=0 → short-circuit dormant; questions fall through to CLI path."""
        from main import process_email
        mocker.patch("main.execute_command", return_value="out")
        mock_reply = mocker.patch("main.send_threaded_reply")

        msg = email.message.EmailMessage()
        msg["From"] = "user@example.com"
        msg["Return-Path"] = "<user@example.com>"
        msg["Subject"] = "AUTH:testsecret q"
        msg["Message-ID"] = "<q003@mail>"
        msg.set_content("Which repo was it pushed to last time?")
        config = {
            "authorized_sender": "user@example.com", "shared_secret": "testsecret",
            "gpg_fingerprint": "", "gpg_home": None,
            "smtp_host": "h", "smtp_port": 465, "username": "u", "password": "p",
            "claude_timeout": 60, "claude_bin": "claude",
            # llm_router NOT set / falsey
        }
        process_email(msg, config)
        # CLI fallback still produces two replies (Running + Result).
        assert mock_reply.call_count == 2

    def test_unauthorized_email_ignored(self, mocker):
        from main import process_email
        mock_execute = mocker.patch("main.execute_command")
        mock_reply = mocker.patch("main.send_threaded_reply")

        msg = _make_unauthorized_msg()
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
        mock_execute.assert_not_called()
        mock_reply.assert_not_called()

    def test_empty_command_body_skipped(self, mocker):
        from main import process_email
        mock_execute = mocker.patch("main.execute_command")
        mock_reply = mocker.patch("main.send_threaded_reply")
        mocker.patch("main.is_authorized", return_value=True)

        msg = email.message.EmailMessage()
        msg["From"] = "user@example.com"
        msg["Return-Path"] = "<user@example.com>"
        msg["Subject"] = "AUTH:testsecret"  # strips to empty; nothing to run
        msg["Message-ID"] = "<test002@mail>"
        msg.set_content("")  # empty body

        config = {
            "authorized_sender": "user@example.com",
            "shared_secret": "testsecret",
            "gpg_fingerprint": "", "gpg_home": None,
            "smtp_host": "h", "smtp_port": 465,
            "username": "u", "password": "p",
            "claude_timeout": 30, "claude_bin": "claude",
        }
        process_email(msg, config)
        mock_execute.assert_not_called()
        mock_reply.assert_not_called()

    def test_subject_only_mail_executes(self, mocker):
        """Phone-style subject-only mail must reach the CLI."""
        from main import process_email
        mock_execute = mocker.patch("main.execute_command", return_value="ok")
        mocker.patch("main.send_threaded_reply")
        mocker.patch("main.is_authorized", return_value=True)

        msg = email.message.EmailMessage()
        msg["From"] = "user@example.com"
        msg["Return-Path"] = "<user@example.com>"
        msg["Subject"] = "Re: AUTH:testsecret list files"
        msg["Message-ID"] = "<sub01@mail>"
        msg.set_content("")

        config = {
            "authorized_sender": "user@example.com",
            "shared_secret": "testsecret",
            "gpg_fingerprint": "", "gpg_home": None,
            "smtp_host": "h", "smtp_port": 465,
            "username": "u", "password": "p",
            "claude_timeout": 30, "claude_bin": "claude",
        }
        process_email(msg, config)
        mock_execute.assert_called_once()
        assert mock_execute.call_args.args[0] == "list files"
