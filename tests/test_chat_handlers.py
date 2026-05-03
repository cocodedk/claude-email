"""Tests for src/chat_handlers.py — focused on gaps in coverage."""
import email.message
import pytest
from unittest.mock import MagicMock, patch


def _make_message(subject="Re: test", msg_id="<orig@mail>"):
    msg = email.message.EmailMessage()
    msg["Subject"] = subject
    msg["Message-ID"] = msg_id
    msg.set_content("body")
    return msg


def _base_config():
    return {
        "smtp_host": "smtp.example.com",
        "smtp_port": 465,
        "username": "claude@example.com",
        "password": "secret",
        "authorized_sender": "bb@example.com",
        "email_domain": "example.com",
        "chat_url": "http://localhost:8420/sse",
        "claude_bin": "claude",
        "claude_cwd": "/tmp",
        "claude_yolo": False,
        "claude_model": None,
        "claude_effort": None,
        "claude_max_budget_usd": None,
        "claude_extra_env": None,
        "service_name_email": "claude-email.service",
        "service_name_chat": "claude-chat.service",
    }


class TestSendThreadedReply:
    """Direct unit tests for send_threaded_reply (lines 33-36)."""

    def test_passes_subject_and_threading_headers(self, mocker):
        """send_threaded_reply must forward Subject, in_reply_to and references."""
        from src.chat_handlers import send_threaded_reply

        mock_send = mocker.patch("src.chat_handlers.send_reply", return_value="<reply@mail>")
        config = _base_config()
        msg = _make_message(subject="Re: my command", msg_id="<original@mail>")

        result = send_threaded_reply(config, msg, "Hello from agent")

        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        assert kwargs["subject"] == "Re: my command"
        assert kwargs["in_reply_to"] == "<original@mail>"
        assert kwargs["references"] == "<original@mail>"
        assert kwargs["body"].startswith("Hello from agent")
        # footer adds the next-action hints; body should end with the marker
        assert "Reply to this email" in kwargs["body"]
        assert result == "<reply@mail>"

    def test_missing_subject_defaults_to_command(self, mocker):
        """A message without Subject header should use 'command' as subject."""
        from src.chat_handlers import send_threaded_reply

        mock_send = mocker.patch("src.chat_handlers.send_reply", return_value="<r@mail>")
        config = _base_config()
        msg = email.message.EmailMessage()
        # No Subject, no Message-ID
        msg.set_content("body")

        send_threaded_reply(config, msg, "reply body")

        kwargs = mock_send.call_args.kwargs
        assert kwargs["subject"] == "command"
        assert kwargs["in_reply_to"] == ""
        assert kwargs["references"] == ""

    def test_uses_email_domain_from_config(self, mocker):
        """email_domain is forwarded from config."""
        from src.chat_handlers import send_threaded_reply

        mock_send = mocker.patch("src.chat_handlers.send_reply", return_value="<r@mail>")
        config = _base_config()
        config["email_domain"] = "custom.domain"
        msg = _make_message()

        send_threaded_reply(config, msg, "body")

        kwargs = mock_send.call_args.kwargs
        assert kwargs["email_domain"] == "custom.domain"

    def test_reply_to_overrides_canonical_sender(self, mocker):
        """When dispatch_by_sender adds ``reply_to`` to the scoped config,
        send_threaded_reply must address the reply to that actual sender,
        not the canonical AUTHORIZED_SENDER. Otherwise alias senders can
        write but never receive."""
        from src.chat_handlers import send_threaded_reply

        mock_send = mocker.patch("src.chat_handlers.send_reply", return_value="<r@mail>")
        config = _base_config()
        config["authorized_senders"] = ["bb@example.com", "alias@example.com"]
        config["reply_to"] = "alias@example.com"
        send_threaded_reply(config, _make_message(), "body")

        kwargs = mock_send.call_args.kwargs
        assert kwargs["to"] == "alias@example.com"

    def test_falls_back_to_canonical_when_reply_to_missing(self, mocker):
        """Direct callers (legacy/test paths) without reply_to keep using
        the canonical sender — preserves back-compat."""
        from src.chat_handlers import send_threaded_reply

        mock_send = mocker.patch("src.chat_handlers.send_reply", return_value="<r@mail>")
        config = _base_config()  # no reply_to
        send_threaded_reply(config, _make_message(), "body")

        kwargs = mock_send.call_args.kwargs
        assert kwargs["to"] == "bb@example.com"


class TestHandleMetaSpawnValueError:
    """Covers lines 109-111: spawn ValueError path."""

    def test_spawn_value_error_sends_rejection_reply(self, mocker):
        """If spawn_agent raises ValueError, send_threaded_reply is called with 'Spawn rejected:'."""
        from src.chat_handlers import _handle_meta
        from src.chat_router import Route

        mocker.patch("src.chat_handlers.spawn_agent", side_effect=ValueError("path not allowed"))
        mock_reply = mocker.patch("src.chat_handlers.send_reply", return_value="<r@mail>")

        config = _base_config()
        msg = _make_message()
        route = Route(
            kind="meta",
            meta_command="spawn",
            meta_args="/some/path an instruction",
            agent_name=None,
            body=None,
            original_message_id=None,
        )
        mock_db = MagicMock()

        _handle_meta(route, config, msg, mock_db)

        mock_reply.assert_called_once()
        kwargs = mock_reply.call_args.kwargs
        assert kwargs["body"].startswith("Spawn rejected: ")
        assert "path not allowed" in kwargs["body"]


class TestSpawnAsName:
    """`spawn <path> as <name>` routes the explicit name through to spawn_agent."""

    def test_as_name_passes_agent_name_to_spawner(self, mocker):
        from src.chat_handlers import _handle_meta
        from src.chat_router import Route

        captured = {}

        def fake_spawn(*args, **kwargs):
            captured["agent_name"] = kwargs.get("agent_name")
            return ("agent-custom", 42)

        mocker.patch("src.chat_handlers.spawn_agent", side_effect=fake_spawn)
        mocker.patch("src.chat_handlers.send_reply", return_value="<r@mail>")

        config = _base_config()
        msg = _make_message()
        route = Route(
            kind="meta",
            meta_command="spawn",
            meta_args="/some/path as agent-custom",
            agent_name=None,
            body=None,
            original_message_id=None,
        )
        _handle_meta(route, config, msg, MagicMock())
        assert captured["agent_name"] == "agent-custom"

    def test_as_name_with_instruction_passes_both(self, mocker):
        from src.chat_handlers import _handle_meta
        from src.chat_router import Route

        captured = {}

        def fake_spawn(*args, **kwargs):
            captured["agent_name"] = kwargs.get("agent_name")
            captured["instruction"] = kwargs.get("instruction")
            return ("agent-custom", 42)

        mocker.patch("src.chat_handlers.spawn_agent", side_effect=fake_spawn)
        mocker.patch("src.chat_handlers.send_reply", return_value="<r@mail>")

        config = _base_config()
        route = Route(
            kind="meta", meta_command="spawn",
            meta_args="/p as agent-custom run all tests",
            agent_name=None, body=None, original_message_id=None,
        )
        _handle_meta(route, config, _make_message(), MagicMock())
        assert captured["agent_name"] == "agent-custom"
        assert captured["instruction"] == "run all tests"

    def test_invalid_agent_name_rejected_with_error_reply(self, mocker):
        from src.chat_handlers import _handle_meta
        from src.chat_router import Route

        spawn_mock = mocker.patch("src.chat_handlers.spawn_agent")
        reply_mock = mocker.patch("src.chat_handlers.send_reply", return_value="<r@mail>")

        config = _base_config()
        route = Route(
            kind="meta", meta_command="spawn",
            meta_args="/p as Not-Valid",
            agent_name=None, body=None, original_message_id=None,
        )
        _handle_meta(route, config, _make_message(), MagicMock())

        spawn_mock.assert_not_called()
        reply_mock.assert_called_once()
        body = reply_mock.call_args.kwargs["body"]
        assert "invalid agent name" in body.lower()
        assert "'Not-Valid'" in body or '"Not-Valid"' in body

    def test_dangling_as_replies_error(self, mocker):
        """`spawn <path> as` with no name token is a typo — the handler
        must NOT spawn the default agent with instruction "as"; it must
        reply with an Error so the user catches the mistake."""
        from src.chat_handlers import _handle_meta
        from src.chat_router import Route

        spawn_mock = mocker.patch("src.chat_handlers.spawn_agent")
        reply_mock = mocker.patch("src.chat_handlers.send_reply", return_value="<r@mail>")

        config = _base_config()
        route = Route(
            kind="meta", meta_command="spawn",
            meta_args="/p as",
            agent_name=None, body=None, original_message_id=None,
        )
        _handle_meta(route, config, _make_message(), MagicMock())

        spawn_mock.assert_not_called()
        reply_mock.assert_called_once()
        body = reply_mock.call_args.kwargs["body"]
        assert body.startswith("Spawn rejected: ")
        assert "missing agent name after 'as'" in body

    def test_legacy_no_as_clause_passes_none_agent_name(self, mocker):
        """The existing `spawn <path> <instruction>` form must still work."""
        from src.chat_handlers import _handle_meta
        from src.chat_router import Route

        captured = {}

        def fake_spawn(*args, **kwargs):
            captured["agent_name"] = kwargs.get("agent_name")
            captured["instruction"] = kwargs.get("instruction")
            return ("agent-default", 42)

        mocker.patch("src.chat_handlers.spawn_agent", side_effect=fake_spawn)
        mocker.patch("src.chat_handlers.send_reply", return_value="<r@mail>")

        config = _base_config()
        route = Route(
            kind="meta", meta_command="spawn",
            meta_args="/p run something",
            agent_name=None, body=None, original_message_id=None,
        )
        _handle_meta(route, config, _make_message(), MagicMock())
        assert captured["agent_name"] is None
        assert captured["instruction"] == "run something"


class TestMaybeCleanupDbExceptionBranch:
    """maybe_cleanup_db (now in src.chat_relay) swallows cleanup_old errors."""

    def test_exception_is_caught_and_logged(self, mocker):
        """cleanup_old raising must be caught; logger.exception must be called."""
        import src.chat_relay as module

        # Reset the cleanup timer so the cleanup actually runs
        original_ts = module._last_cleanup_ts
        module._last_cleanup_ts = 0.0

        try:
            mock_db = MagicMock()
            mock_db.cleanup_old.side_effect = RuntimeError("db error")
            mock_log = mocker.patch("src.chat_relay.logger")

            from src.chat_handlers import maybe_cleanup_db  # still re-exported
            maybe_cleanup_db(mock_db)  # must not raise

            mock_log.exception.assert_called_once()
        finally:
            module._last_cleanup_ts = original_ts
