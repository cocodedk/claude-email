"""Tests for chat integration in main.py and src/chat_handlers.py."""
import email.message
import tempfile
import os
import pytest

from src.chat_db import ChatDB


def _make_config(secret="testsecret"):
    return {
        "authorized_sender": "bb@cocode.dk",
        "shared_secret": secret,
        "gpg_fingerprint": "",
        "gpg_home": None,
        "smtp_host": "send.one.com",
        "smtp_port": 465,
        "username": "claude@cocode.dk",
        "password": "pw",
        "claude_timeout": 30,
        "claude_bin": "claude",
        "auth_prefix": f"AUTH:{secret}",
        "chat_url": "http://localhost:8420/sse",
    }


def _make_msg(subject, body, from_addr="bb@cocode.dk", msg_id="<test001@mail>",
              in_reply_to=""):
    msg = email.message.EmailMessage()
    msg["From"] = f"Babak <{from_addr}>"
    msg["Return-Path"] = f"<{from_addr}>"
    msg["Subject"] = subject
    msg["Message-ID"] = msg_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    msg.set_content(body)
    return msg


@pytest.fixture
def chat_db(tmp_path):
    db_path = str(tmp_path / "test-chat.db")
    return ChatDB(db_path)


class TestProcessEmailChatReply:
    def test_process_email_chat_reply_inserts_reply(self, mocker, chat_db):
        """When an email is a reply to a known agent message, insert it as a reply in the DB."""
        from main import process_email

        # Pre-populate: an agent sent a message that was emailed with a known Message-ID
        original = chat_db.insert_message("agent-foo", "user", "Hello user", "chat")
        chat_db.set_email_message_id(original["id"], "<agent-msg-001@mail>")

        mocker.patch("main.is_authorized", return_value=True)
        mocker.patch("main.send_reply")
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


class TestProcessEmailAgentCommand:
    def test_process_email_agent_command_dispatched(self, mocker, chat_db):
        """When subject starts with @agent-name, route as agent command."""
        from main import process_email

        # Register the agent in DB so it exists
        chat_db.register_agent("agent-foo", "/tmp/foo")

        mocker.patch("main.is_authorized", return_value=True)
        mock_reply = mocker.patch("src.chat_handlers.send_reply")
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


class TestProcessEmailCLIFallback:
    def test_process_email_cli_fallback(self, mocker, chat_db):
        """Normal CLI command still works when chat_db is provided."""
        from main import process_email

        mocker.patch("main.is_authorized", return_value=True)
        mock_execute = mocker.patch("main.execute_command", return_value="output")
        mock_reply = mocker.patch("main.send_reply")

        msg = _make_msg(
            subject="AUTH:testsecret list files",
            body="list files in /tmp",
        )

        config = _make_config()
        process_email(msg, config, chat_db=chat_db)

        # Should fall through to CLI execution
        mock_execute.assert_called_once()
        mock_reply.assert_called_once()


class TestProcessEmailNoChatDB:
    def test_process_email_no_chat_db_works(self, mocker):
        """Backward compatibility: no chat_db param = old CLI-only behavior."""
        from main import process_email

        mock_execute = mocker.patch("main.execute_command", return_value="file list output")
        mock_reply = mocker.patch("main.send_reply")

        msg = _make_msg(
            subject="AUTH:testsecret list files",
            body="list files in /tmp",
        )

        # Config WITHOUT chat-specific keys (like existing tests)
        config = {
            "authorized_sender": "bb@cocode.dk",
            "shared_secret": "testsecret",
            "gpg_fingerprint": "",
            "gpg_home": None,
            "smtp_host": "send.one.com",
            "smtp_port": 465,
            "username": "claude@cocode.dk",
            "password": "pw",
            "claude_timeout": 30,
            "claude_bin": "claude",
        }
        process_email(msg, config)
        mock_execute.assert_called_once()
        mock_reply.assert_called_once()


class TestRelayOutboundMessages:
    def test_relay_outbound_messages(self, mocker, chat_db):
        """Pending agent messages get sent as emails and marked delivered."""
        from src.chat_handlers import relay_outbound_messages

        mock_reply = mocker.patch("src.chat_handlers.send_reply")

        # Agent sends a message to user
        chat_db.insert_message("agent-foo", "user", "Build succeeded!", "chat")
        chat_db.insert_message("agent-bar", "user", "Tests all pass", "chat")

        config = _make_config()
        relay_outbound_messages(config, chat_db)

        assert mock_reply.call_count == 2
        # Both should now be delivered
        assert chat_db.get_pending_messages_for("user") == []
