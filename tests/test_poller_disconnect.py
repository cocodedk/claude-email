"""Tests for IMAP email polling — disconnect behavior."""
import email.message
import imaplib
import json
import ssl
import tempfile
from pathlib import Path
import pytest
from unittest.mock import MagicMock, patch, call
from src.poller import EmailPoller
from tests._poller_helpers import _mock_imap, _make_uid_handler


class TestEmailPoller:
    def test_disconnect_when_not_connected(self, tmp_path):
        poller = EmailPoller(
            host="h", port=993, username="u", password="p",
            state_file=str(tmp_path / "ids.json"),
        )
        poller.disconnect()  # should not raise

    def test_disconnect_handles_close_exception(self, mocker, tmp_path):
        mocker.patch("ssl.create_default_context", return_value=MagicMock())
        mock_class, mock_conn = _mock_imap(mocker)
        mock_conn.close.side_effect = Exception("mailbox not selected")
        mock_conn.logout.return_value = ("BYE", [b"bye"])

        poller = EmailPoller(
            host="h", port=993, username="u", password="p",
            state_file=str(tmp_path / "ids.json"),
        )
        poller.connect()
        poller.disconnect()  # should not raise
        assert poller._conn is None

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

    def test_disconnect_handles_logout_exception(self, mocker, tmp_path):
        """If both close() and logout() raise, disconnect still succeeds."""
        mocker.patch("ssl.create_default_context", return_value=MagicMock())
        mock_class, mock_conn = _mock_imap(mocker)
        mock_conn.close.side_effect = Exception("close failed")
        mock_conn.logout.side_effect = Exception("logout failed")

        poller = EmailPoller(
            host="h", port=993, username="u", password="p",
            state_file=str(tmp_path / "ids.json"),
        )
        poller.connect()
        poller.disconnect()  # should not raise
        assert poller._conn is None
