"""Tests for src/chat_handlers.py — reaction routing in _handle_reply."""
import email.message
import pytest
from unittest.mock import MagicMock, patch

from tests._chat_handlers_helpers import _make_message, _base_config


class TestHandleReplyReactionRouting:
    """_handle_reply must route reaction bodies through reaction_router,
    never calling apply_reply (no task, no branch, no dirty check)."""

    def _make_reply_route(self, original_message_id=42, agent_name="agent-foo"):
        from src.chat_router import Route
        return Route(
            kind="chat_reply",
            agent_name=agent_name,
            original_message_id=original_message_id,
            body=None,
            meta_command=None,
            meta_args=None,
        )

    def test_reaction_body_never_calls_apply_reply(self, mocker):
        """A '[like] X reacted to your message' body must NOT reach apply_reply."""
        from src.chat_handlers import _handle_reply

        apply_mock = mocker.patch("src.chat_handlers.apply_reply")
        mocker.patch(
            "src.chat_handlers.extract_reaction", return_value="like",
        )
        mocker.patch(
            "src.chat_handlers.handle_reaction",
            return_value=("Got your like — noted, no work queued.", "Reaction"),
        )
        mocker.patch("src.chat_handlers.send_reply", return_value="<r@mail>")

        msg = _make_message()
        msg["In-Reply-To"] = "<orig@mail>"
        db = MagicMock()

        _handle_reply(
            self._make_reply_route(), msg, _base_config(), db, None, None,
        )

        apply_mock.assert_not_called()

    def test_reaction_body_calls_handle_reaction(self, mocker):
        """handle_reaction must be called with correct kwargs."""
        from src.chat_handlers import _handle_reply

        mocker.patch("src.chat_handlers.apply_reply")
        mocker.patch(
            "src.chat_handlers.extract_reaction", return_value="heart",
        )
        handle_mock = mocker.patch(
            "src.chat_handlers.handle_reaction",
            return_value=("Got your heart — noted, no work queued.", "Reaction"),
        )
        mocker.patch("src.chat_handlers.send_reply", return_value="<r@mail>")

        msg = _make_message()
        db = MagicMock()
        route = self._make_reply_route(original_message_id=7, agent_name="agent-bar")

        _handle_reply(route, msg, _base_config(), db, None, None)

        handle_mock.assert_called_once()
        call_kwargs = handle_mock.call_args.kwargs
        assert call_kwargs["agent_name"] == "agent-bar"
        assert call_kwargs["original_message_id"] == 7
        assert call_kwargs["reaction"] == "heart"

    def test_reaction_ack_sent_back_to_user(self, mocker):
        """The ACK email must contain the text returned by handle_reaction."""
        from src.chat_handlers import _handle_reply

        mocker.patch("src.chat_handlers.apply_reply")
        mocker.patch(
            "src.chat_handlers.extract_reaction", return_value="like",
        )
        mocker.patch(
            "src.chat_handlers.handle_reaction",
            return_value=("Got your like — noted, no work queued.", "Reaction"),
        )
        send_mock = mocker.patch("src.chat_handlers.send_reply", return_value="<r@mail>")

        msg = _make_message()
        db = MagicMock()

        _handle_reply(
            self._make_reply_route(), msg, _base_config(), db, None, None,
        )

        send_mock.assert_called_once()
        body_sent = send_mock.call_args.kwargs["body"]
        assert "noted, no work queued" in body_sent

    def test_non_reaction_body_still_calls_apply_reply(self, mocker):
        """Normal reply bodies must still flow through apply_reply unchanged."""
        from src.chat_handlers import _handle_reply

        apply_mock = mocker.patch(
            "src.chat_handlers.apply_reply",
            return_value=("Delivered to agent-foo on the chat bus.", "Delivered"),
        )
        mocker.patch(
            "src.chat_handlers.extract_reaction", return_value=None,
        )
        mocker.patch("src.chat_handlers.send_reply", return_value="<r@mail>")

        msg = _make_message()
        db = MagicMock()

        _handle_reply(
            self._make_reply_route(), msg, _base_config(), db, None, None,
        )

        apply_mock.assert_called_once()
