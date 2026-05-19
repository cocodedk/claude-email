"""Tests for chat integration in main.py — process_email dispatch paths."""
import email.message
import tempfile
import os
import pytest

from src.chat_db import ChatDB
from tests._main_chat_helpers import _make_config, _make_msg, chat_db


class TestProcessEmailChatReply:
    def test_process_email_chat_reply_inserts_reply(self, mocker, chat_db):
        """When an email is a reply to a known agent message, insert it as a reply in the DB."""
        from main import process_email

        # Pre-populate: an agent sent a message that was emailed with a known Message-ID
        original = chat_db.insert_message("agent-foo", "user", "Hello user", "chat")
        chat_db.set_email_message_id(original["id"], "<agent-msg-001@mail>")

        mocker.patch("main.is_authorized", return_value=True)
        mocker.patch("src.chat_handlers.send_threaded_reply")
        mock_execute = mocker.patch("main.execute_command")

        msg = _make_msg(
            subject="Re: agent-foo message",
            body="Thanks, that looks good",
            in_reply_to="<agent-msg-001@mail>",
        )

        config = _make_config()
        process_email(msg, config, chat_db=chat_db)

        # The reply should be inserted in the DB, not executed as CLI
        mock_execute.assert_not_called()
        pending = chat_db.get_pending_messages_for("agent-foo")
        assert len(pending) == 1
        assert pending[0]["body"] == "Thanks, that looks good"
        assert pending[0]["from_name"] == "user"


class TestProcessEmailPlainTextAuthRejection:
    def test_envelope_passes_but_plain_text_auth_fails(self, mocker, chat_db):
        """Sender is allowed (envelope OK) but no AUTH:<secret> in body/subject
        and no GPG — is_authorized returns False → dropped with plain-text log."""
        from main import process_email
        mocker.patch("main.identify_sender", return_value="user@example.com")
        mocker.patch("main.is_authorized", return_value=False)
        mock_execute = mocker.patch("main.execute_command")
        mock_logger = mocker.patch("main.logger")
        msg = email.message.Message()
        msg["Message-ID"] = "<x@x>"
        msg.set_payload("no auth prefix")
        config = _make_config()
        process_email(msg, config, chat_db=chat_db)
        mock_execute.assert_not_called()
        # second-gate drop log fires
        warn = [c.args[0] for c in mock_logger.warning.call_args_list]
        assert any("plain-text auth" in m for m in warn)


class TestProcessEmailJsonMode:
    def test_json_email_routes_through_json_handler(self, mocker, chat_db):
        import json
        from main import process_email
        mocker.patch("main.identify_sender", return_value="user@example.com")
        handler = mocker.patch("main.handle_json_email")
        mocker.patch("main.handle_chat_email")
        msg = email.message.Message()
        msg.add_header("Content-Type", "application/json")
        msg.set_payload(json.dumps({"v": 1, "kind": "command", "body": "x"}))
        config = _make_config()
        process_email(msg, config, chat_db=chat_db, task_queue=object(), worker_manager=object())
        handler.assert_called_once()


class TestProcessEmailAgentCommand:
    def test_process_email_agent_command_dispatched(self, mocker, chat_db):
        """When subject starts with @agent-name, route as agent command."""
        from main import process_email

        # Register the agent in DB so it exists
        chat_db.register_agent("agent-foo", "/tmp/foo")

        mocker.patch("main.is_authorized", return_value=True)
        mock_reply = mocker.patch("src.chat_handlers.send_threaded_reply")
        mock_execute = mocker.patch("main.execute_command")

        msg = _make_msg(
            subject="AUTH:testsecret @agent-foo",
            body="run the tests please",
        )

        config = _make_config()
        process_email(msg, config, chat_db=chat_db)

        # Should NOT run CLI execute
        mock_execute.assert_not_called()
        # Should insert message for agent-foo in DB
        pending = chat_db.get_pending_messages_for("agent-foo")
        assert len(pending) == 1
        assert pending[0]["body"] == "run the tests please"
        assert pending[0]["from_name"] == "user"
        # Should send confirmation reply
        mock_reply.assert_called_once()


    def test_unknown_agent_rejected_not_silently_queued(self, mocker, chat_db):
        """@<agent> with no matching row must bounce back to the user, not
        queue a message for a nonexistent inbox. Typos would otherwise
        leave undeliverable rows that the wake-watcher polls forever and
        the user gets an empty 'Dispatched' ack despite the target never
        existing."""
        from main import process_email

        # Register a different agent so we can assert the error reply
        # lists known agents to help the user correct their typo.
        chat_db.register_agent("agent-known", "/tmp/known")

        mocker.patch("main.is_authorized", return_value=True)
        mock_reply = mocker.patch("src.chat_handlers.send_threaded_reply")
        mock_execute = mocker.patch("main.execute_command")

        msg = _make_msg(
            subject="AUTH:testsecret @agent-typo",
            body="run the tests",
        )
        config = _make_config()
        process_email(msg, config, chat_db=chat_db)

        # No pending message queued for the phantom agent
        assert chat_db.get_pending_messages_for("agent-typo") == []
        # Not treated as a CLI fallback either — it's still a chat-routed
        # @agent command, just a rejected one.
        mock_execute.assert_not_called()
        mock_reply.assert_called_once()
        body = mock_reply.call_args[0][2]
        tag = mock_reply.call_args.kwargs.get("tag", "")
        assert "unknown" in body.lower() or "no such" in body.lower()
        assert "agent-typo" in body
        assert "agent-known" in body  # hint at valid targets
        assert tag != "Dispatched"


class TestProcessEmailCLIFallback:
    def test_process_email_cli_fallback(self, mocker, chat_db):
        """Normal CLI command still works when chat_db is provided."""
        from main import process_email

        mocker.patch("main.is_authorized", return_value=True)
        mock_execute = mocker.patch("main.execute_command", return_value="output")
        mock_reply = mocker.patch("main.send_threaded_reply")

        msg = _make_msg(
            subject="AUTH:testsecret list files",
            body="list files in /tmp",
        )

        config = _make_config()
        process_email(msg, config, chat_db=chat_db)

        # Should fall through to CLI execution
        mock_execute.assert_called_once()
        # Two replies: progress ack + final output
        assert mock_reply.call_count == 2


class TestProcessEmailNoChatDB:
    def test_process_email_no_chat_db_works(self, mocker):
        """Backward compatibility: no chat_db param = old CLI-only behavior."""
        from main import process_email

        mock_execute = mocker.patch("main.execute_command", return_value="file list output")
        mock_reply = mocker.patch("main.send_threaded_reply")

        msg = _make_msg(
            subject="AUTH:testsecret list files",
            body="list files in /tmp",
        )

        # Config WITHOUT chat-specific keys (like existing tests)
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
        # Two replies: progress ack + final output
        assert mock_reply.call_count == 2
