"""Integration tests for the main orchestration loop."""
import email.message
import signal
import pytest
from unittest.mock import MagicMock, patch, call


def _make_authorized_msg(secret: str = "testsecret") -> email.message.Message:
    msg = email.message.EmailMessage()
    msg["From"] = "Babak <bb@cocode.dk>"
    msg["Return-Path"] = "<bb@cocode.dk>"
    msg["Subject"] = f"AUTH:{secret} list files"
    msg["Message-ID"] = "<test001@mail>"
    msg.set_content("list files in /tmp")
    return msg


def _make_unauthorized_msg() -> email.message.Message:
    msg = email.message.EmailMessage()
    msg["From"] = "hacker@evil.com"
    msg["Return-Path"] = "<hacker@evil.com>"
    msg["Subject"] = "run rm -rf /"
    msg["Message-ID"] = "<evil001@mail>"
    msg.set_content("rm -rf /")
    return msg


class TestOrchestration:
    def test_authorized_email_triggers_execution(self, mocker):
        from main import process_email
        mock_execute = mocker.patch("main.execute_command", return_value="file list output")
        mock_reply = mocker.patch("main.send_reply")

        msg = _make_authorized_msg("testsecret")
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
        }
        process_email(msg, config)
        mock_execute.assert_called_once()
        mock_reply.assert_called_once()

    def test_unauthorized_email_ignored(self, mocker):
        from main import process_email
        mock_execute = mocker.patch("main.execute_command")
        mock_reply = mocker.patch("main.send_reply")

        msg = _make_unauthorized_msg()
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
        }
        process_email(msg, config)
        mock_execute.assert_not_called()
        mock_reply.assert_not_called()
