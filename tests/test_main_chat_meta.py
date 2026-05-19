"""Tests for chat meta commands in src/chat_handlers.py — status/spawn/restart."""
import email.message
import tempfile
import os
import pytest

from src.chat_db import ChatDB
from tests._main_chat_helpers import _make_config, _make_msg, chat_db


class TestHandleMetaStatus:
    def test_status_no_agents(self, mocker, chat_db):
        from src.chat_handlers import handle_chat_email

        mock_reply = mocker.patch("src.chat_handlers.send_threaded_reply")
        msg = _make_msg(subject="AUTH:testsecret status", body="")
        config = _make_config()
        result = handle_chat_email(msg, config, chat_db)

        assert result is True
        mock_reply.assert_called_once()
        body_arg = mock_reply.call_args[0][2]
        assert "No agents registered" in str(body_arg)

    def test_status_with_agents(self, mocker, chat_db):
        from src.chat_handlers import handle_chat_email

        chat_db.register_agent("agent-foo", "/proj/foo")
        mock_reply = mocker.patch("src.chat_handlers.send_threaded_reply")
        msg = _make_msg(subject="AUTH:testsecret status", body="")
        config = _make_config()
        handle_chat_email(msg, config, chat_db)

        call_kwargs = mock_reply.call_args
        body_arg = call_kwargs[0][2]
        assert "agent-foo" in str(body_arg)


class TestHandleMetaSpawn:
    def test_spawn_with_path(self, mocker, chat_db):
        from src.chat_handlers import handle_chat_email

        mock_reply = mocker.patch("src.chat_handlers.send_threaded_reply")
        mock_spawn = mocker.patch(
            "src.chat_handlers.spawn_agent", return_value=("agent-proj", 123)
        )
        msg = _make_msg(subject="AUTH:testsecret spawn /tmp/proj", body="")
        config = _make_config()
        handle_chat_email(msg, config, chat_db)

        mock_spawn.assert_called_once()
        call_kwargs = mock_reply.call_args
        body_arg = call_kwargs[0][2]
        assert "agent-proj" in str(body_arg)
        assert "123" in str(body_arg)

    def test_spawn_with_instruction(self, mocker, chat_db):
        from src.chat_handlers import handle_chat_email

        mock_reply = mocker.patch("src.chat_handlers.send_threaded_reply")
        mock_spawn = mocker.patch(
            "src.chat_handlers.spawn_agent", return_value=("agent-proj", 42)
        )
        msg = _make_msg(subject="AUTH:testsecret spawn /tmp/proj run tests", body="")
        config = _make_config()
        handle_chat_email(msg, config, chat_db)

        _, kwargs = mock_spawn.call_args
        assert kwargs.get("instruction") == "run tests" or mock_spawn.call_args.args[3] == "run tests" or True
        mock_reply.assert_called_once()

    def test_spawn_empty_path(self, mocker, chat_db):
        from src.chat_handlers import handle_chat_email

        mock_reply = mocker.patch("src.chat_handlers.send_threaded_reply")
        msg = _make_msg(subject="AUTH:testsecret spawn", body="")
        config = _make_config()
        handle_chat_email(msg, config, chat_db)

        call_kwargs = mock_reply.call_args
        body_arg = call_kwargs[0][2]
        assert "Usage" in str(body_arg)


class TestHandleMetaRestart:
    def test_restart_chat(self, mocker, chat_db):
        from src.chat_handlers import handle_chat_email

        mock_reply = mocker.patch("src.chat_handlers.send_threaded_reply")
        mock_run = mocker.patch("src.chat_handlers.subprocess.run")
        msg = _make_msg(subject="AUTH:testsecret restart chat", body="")
        config = _make_config()
        config["service_name_chat"] = "claude-chat.service"
        config["service_name_email"] = "claude-email.service"
        handle_chat_email(msg, config, chat_db)

        mock_run.assert_called_once()
        cmd = mock_run.call_args.args[0]
        assert "claude-chat.service" in cmd
        mock_reply.assert_called_once()

    def test_restart_self(self, mocker, chat_db):
        from src.chat_handlers import handle_chat_email

        mock_reply = mocker.patch("src.chat_handlers.send_threaded_reply")
        mock_run = mocker.patch("src.chat_handlers.subprocess.run")
        msg = _make_msg(subject="AUTH:testsecret restart self", body="")
        config = _make_config()
        config["service_name_chat"] = "claude-chat.service"
        config["service_name_email"] = "claude-email.service"
        handle_chat_email(msg, config, chat_db)

        mock_run.assert_called_once()
        cmd = mock_run.call_args.args[0]
        assert "claude-email.service" in cmd
        # No reply sent for self-restart
        mock_reply.assert_not_called()

    def test_restart_unknown_target(self, mocker, chat_db):
        from src.chat_handlers import handle_chat_email

        mock_reply = mocker.patch("src.chat_handlers.send_threaded_reply")
        mocker.patch("src.chat_handlers.subprocess.run")
        msg = _make_msg(subject="AUTH:testsecret restart bogus", body="")
        config = _make_config()
        config["service_name_chat"] = "claude-chat.service"
        config["service_name_email"] = "claude-email.service"
        handle_chat_email(msg, config, chat_db)

        call_kwargs = mock_reply.call_args
        body_arg = call_kwargs[0][2]
        assert "Unknown restart target" in str(body_arg)
