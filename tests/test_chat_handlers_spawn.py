"""Tests for src/chat_handlers.py — spawn meta routing (gaps in coverage)."""
import email.message
import pytest
from unittest.mock import MagicMock, patch

from tests._chat_handlers_helpers import _make_message, _base_config


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
