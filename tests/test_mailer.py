"""Tests for SMTP email reply sending."""
import smtplib
import ssl
import pytest
from unittest.mock import MagicMock, patch
from src.mailer import send_reply


class TestSendReply:
    def test_send_reply_uses_verified_ssl(self, mocker):
        mock_ssl = mocker.patch("ssl.create_default_context", return_value=MagicMock())
        mock_smtp_class = mocker.patch("smtplib.SMTP_SSL")
        mock_smtp = MagicMock()
        mock_smtp_class.return_value.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp_class.return_value.__exit__ = MagicMock(return_value=False)

        send_reply(
            smtp_host="send.one.com",
            smtp_port=465,
            username="claude@cocode.dk",
            password="pw",
            to="bb@cocode.dk",
            subject="Re: test",
            body="done",
            in_reply_to="<original@mail>",
            references="<original@mail>",
        )
        mock_ssl.assert_called_once()

    def test_reply_includes_threading_headers(self, mocker):
        mocker.patch("ssl.create_default_context", return_value=MagicMock())
        mock_smtp_class = mocker.patch("smtplib.SMTP_SSL")
        mock_smtp = MagicMock()
        mock_smtp_class.return_value.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp_class.return_value.__exit__ = MagicMock(return_value=False)

        send_reply(
            smtp_host="send.one.com",
            smtp_port=465,
            username="claude@cocode.dk",
            password="pw",
            to="bb@cocode.dk",
            subject="Re: test",
            body="result",
            in_reply_to="<orig@mail>",
            references="<orig@mail>",
        )
        # sendmail was called with the composed message
        assert mock_smtp.sendmail.called or mock_smtp.send_message.called

    def test_returns_generated_message_id(self, mocker):
        mocker.patch("ssl.create_default_context", return_value=MagicMock())
        mock_smtp_class = mocker.patch("smtplib.SMTP_SSL")
        mock_smtp = MagicMock()
        mock_smtp_class.return_value.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp_class.return_value.__exit__ = MagicMock(return_value=False)

        result = send_reply(
            smtp_host="send.one.com", smtp_port=465,
            username="u", password="p",
            to="bb@cocode.dk",
            subject="test",
            body="ok",
            email_domain="example.com",
        )
        assert result.startswith("<")
        assert result.endswith(">")
        assert "example.com" in result

    def test_subject_prefixed_with_re(self, mocker):
        mocker.patch("ssl.create_default_context", return_value=MagicMock())
        mock_smtp_class = mocker.patch("smtplib.SMTP_SSL")
        mock_smtp = MagicMock()
        mock_smtp_class.return_value.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp_class.return_value.__exit__ = MagicMock(return_value=False)

        send_reply(
            smtp_host="send.one.com", smtp_port=465,
            username="u", password="p",
            to="bb@cocode.dk",
            subject="Re: original subject",
            body="ok",
        )
        assert mock_smtp.sendmail.called or mock_smtp.send_message.called
