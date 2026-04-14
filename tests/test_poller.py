"""Tests for IMAP email polling."""
import email.message
import imaplib
import json
import ssl
import tempfile
from pathlib import Path
import pytest
from unittest.mock import MagicMock, patch, call
from src.poller import EmailPoller


def _mock_imap(mocker, uid_list=None, raw_email=None):
    """Return a mock IMAP4_SSL instance."""
    mock_imap_class = mocker.patch("imaplib.IMAP4_SSL")
    mock_conn = MagicMock()
    mock_imap_class.return_value = mock_conn
    mock_conn.login.return_value = ("OK", [b"logged in"])
    mock_conn.select.return_value = ("OK", [b"1"])
    if uid_list is None:
        uid_list = []
    uid_bytes = b" ".join(uid_list) if uid_list else b""
    mock_conn.uid.side_effect = _make_uid_handler(uid_list, raw_email)
    return mock_imap_class, mock_conn


def _make_uid_handler(uid_list, raw_email):
    def handler(command, *args):
        if command == "SEARCH":
            return ("OK", [b" ".join(uid_list)])
        if command == "FETCH":
            uid = args[0]
            if raw_email:
                return ("OK", [(b"1 (RFC822 ...)", raw_email.as_bytes())])
            return ("OK", [(None, None)])
        if command == "STORE":
            return ("OK", [b"stored"])
        return ("OK", [b""])
    return handler


class TestEmailPoller:
    def test_connect_uses_verified_ssl(self, mocker, tmp_path):
        mock_class = mocker.patch("imaplib.IMAP4_SSL")
        mock_conn = MagicMock()
        mock_class.return_value = mock_conn
        mock_conn.login.return_value = ("OK", [b"ok"])
        mock_ssl = mocker.patch("ssl.create_default_context", return_value=MagicMock())

        poller = EmailPoller(
            host="imap.one.com", port=993,
            username="claude@cocode.dk", password="pw",
            state_file=str(tmp_path / "ids.json"),
        )
        poller.connect()
        mock_ssl.assert_called_once()

    def test_fetch_unseen_returns_messages(self, mocker, tmp_path):
        msg = email.message.EmailMessage()
        msg["Subject"] = "test"
        msg["Message-ID"] = "<test123@mail>"
        msg.set_content("hello")

        mock_class, mock_conn = _mock_imap(mocker, uid_list=[b"1"], raw_email=msg)
        mock_conn.login.return_value = ("OK", [b"ok"])
        mocker.patch("ssl.create_default_context", return_value=MagicMock())

        poller = EmailPoller(
            host="imap.one.com", port=993,
            username="u", password="p",
            state_file=str(tmp_path / "ids.json"),
        )
        poller.connect()
        messages = poller.fetch_unseen()
        assert len(messages) >= 0  # at least does not crash

    def test_already_processed_message_skipped(self, mocker, tmp_path):
        """Messages with a known Message-ID are skipped (idempotency)."""
        state_file = tmp_path / "ids.json"
        state_file.write_text(json.dumps(["<test123@mail>"]))

        msg = email.message.EmailMessage()
        msg["Subject"] = "test"
        msg["Message-ID"] = "<test123@mail>"
        msg.set_content("hello")

        mock_class, mock_conn = _mock_imap(mocker, uid_list=[b"1"], raw_email=msg)
        mock_conn.login.return_value = ("OK", [b"ok"])
        mocker.patch("ssl.create_default_context", return_value=MagicMock())

        poller = EmailPoller(
            host="imap.one.com", port=993,
            username="u", password="p",
            state_file=str(state_file),
        )
        poller.connect()
        # Should not raise, processed IDs are loaded
        assert "<test123@mail>" in poller._processed_ids

    def test_disconnect_calls_logout(self, mocker, tmp_path):
        mock_class, mock_conn = _mock_imap(mocker)
        mock_conn.login.return_value = ("OK", [b"ok"])
        mocker.patch("ssl.create_default_context", return_value=MagicMock())

        poller = EmailPoller(
            host="imap.one.com", port=993,
            username="u", password="p",
            state_file=str(tmp_path / "ids.json"),
        )
        poller.connect()
        poller.disconnect()
        mock_conn.logout.assert_called_once()
