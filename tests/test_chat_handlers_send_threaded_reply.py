"""Tests for src/chat_handlers.py — send_threaded_reply (gaps in coverage)."""
import email.message
import pytest
from unittest.mock import patch

from tests._chat_handlers_helpers import _make_message, _base_config


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
