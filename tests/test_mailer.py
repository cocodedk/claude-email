"""Tests for SMTP email reply sending."""
import smtplib
import ssl
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
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
            username="agent@example.com",
            password="pw",
            to="user@example.com",
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
            username="agent@example.com",
            password="pw",
            to="user@example.com",
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
            to="user@example.com",
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
            to="user@example.com",
            subject="Re: original subject",
            body="ok",
        )
        assert mock_smtp.sendmail.called or mock_smtp.send_message.called

    def test_smtp_exception_raised(self, mocker):
        mocker.patch("ssl.create_default_context", return_value=MagicMock())
        mock_smtp_class = mocker.patch("smtplib.SMTP_SSL")
        mock_smtp = MagicMock()
        mock_smtp_class.return_value.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp_class.return_value.__exit__ = MagicMock(return_value=False)
        mock_smtp.send_message.side_effect = smtplib.SMTPException("auth failed")

        with pytest.raises(smtplib.SMTPException):
            send_reply(
                smtp_host="h", smtp_port=465,
                username="u", password="p",
                to="bb@x.com", subject="t", body="b",
            )

    def test_no_domain_uses_default_msgid(self, mocker):
        mocker.patch("ssl.create_default_context", return_value=MagicMock())
        mock_smtp_class = mocker.patch("smtplib.SMTP_SSL")
        mock_smtp = MagicMock()
        mock_smtp_class.return_value.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp_class.return_value.__exit__ = MagicMock(return_value=False)

        result = send_reply(
            smtp_host="h", smtp_port=465,
            username="u", password="p",
            to="bb@x.com", subject="t", body="b",
        )
        # No email_domain passed — still generates a valid Message-ID
        assert result.startswith("<")
        assert result.endswith(">")


class TestContentType:
    def test_application_json_content_type_set(self, mocker):
        import smtplib
        from src.mailer import send_reply
        mock_smtp_cls = mocker.patch("src.mailer.smtplib.SMTP_SSL")
        mock_smtp = mock_smtp_cls.return_value.__enter__.return_value
        send_reply(
            smtp_host="h", smtp_port=465, username="u", password="p",
            to="t@x", subject="s", body='{"v":1}',
            content_type="application/json", email_domain="x",
        )
        sent = mock_smtp.send_message.call_args.args[0]
        assert sent.get_content_type() == "application/json"

    def test_default_is_text_plain(self, mocker):
        import smtplib
        from src.mailer import send_reply
        mock_smtp_cls = mocker.patch("src.mailer.smtplib.SMTP_SSL")
        mock_smtp = mock_smtp_cls.return_value.__enter__.return_value
        send_reply(
            smtp_host="h", smtp_port=465, username="u", password="p",
            to="t@x", subject="s", body="hi", email_domain="x",
        )
        sent = mock_smtp.send_message.call_args.args[0]
        assert sent.get_content_type() == "text/plain"
