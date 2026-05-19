"""Tests for IMAP email polling — fetch_unseen behavior."""
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
    def test_fetch_unseen_not_connected_raises(self, tmp_path):
        poller = EmailPoller(
            host="h", port=993, username="u", password="p",
            state_file=str(tmp_path / "ids.json"),
        )
        with pytest.raises(RuntimeError, match="Not connected"):
            poller.fetch_unseen()

    def test_fetch_unseen_no_results(self, mocker, tmp_path):
        mock_class, mock_conn = _mock_imap(mocker, uid_list=[])
        mocker.patch("ssl.create_default_context", return_value=MagicMock())

        poller = EmailPoller(
            host="h", port=993, username="u", password="p",
            state_file=str(tmp_path / "ids.json"),
        )
        poller.connect()
        assert poller.fetch_unseen() == []

    def test_fetch_unseen_bad_fetch_skipped(self, mocker, tmp_path):
        """If FETCH returns bad data, the message is skipped."""
        mocker.patch("ssl.create_default_context", return_value=MagicMock())
        mock_class = mocker.patch("imaplib.IMAP4_SSL")
        mock_conn = MagicMock()
        mock_class.return_value = mock_conn
        mock_conn.login.return_value = ("OK", [b"ok"])
        mock_conn.select.return_value = ("OK", [b"1"])

        def handler(cmd, *args):
            if cmd == "SEARCH":
                return ("OK", [b"1"])
            if cmd == "FETCH":
                return ("OK", [(None, None)])  # bad fetch
            return ("OK", [b""])
        mock_conn.uid.side_effect = handler

        poller = EmailPoller(
            host="h", port=993, username="u", password="p",
            state_file=str(tmp_path / "ids.json"),
        )
        poller.connect()
        assert poller.fetch_unseen() == []

    def test_fetch_unseen_non_bytes_payload_skipped(self, mocker, tmp_path):
        """If raw payload is not bytes, skip it."""
        mocker.patch("ssl.create_default_context", return_value=MagicMock())
        mock_class = mocker.patch("imaplib.IMAP4_SSL")
        mock_conn = MagicMock()
        mock_class.return_value = mock_conn
        mock_conn.login.return_value = ("OK", [b"ok"])
        mock_conn.select.return_value = ("OK", [b"1"])

        def handler(cmd, *args):
            if cmd == "SEARCH":
                return ("OK", [b"1"])
            if cmd == "FETCH":
                return ("OK", [(b"1 (RFC822 ...)", "not bytes")])
            return ("OK", [b""])
        mock_conn.uid.side_effect = handler

        poller = EmailPoller(
            host="h", port=993, username="u", password="p",
            state_file=str(tmp_path / "ids.json"),
        )
        poller.connect()
        assert poller.fetch_unseen() == []

    def test_fetch_unseen_bad_status_skipped(self, mocker, tmp_path):
        """If FETCH returns non-OK status, the message is skipped."""
        mocker.patch("ssl.create_default_context", return_value=MagicMock())
        mock_class = mocker.patch("imaplib.IMAP4_SSL")
        mock_conn = MagicMock()
        mock_class.return_value = mock_conn
        mock_conn.login.return_value = ("OK", [b"ok"])
        mock_conn.select.return_value = ("OK", [b"1"])

        def handler(cmd, *args):
            if cmd == "SEARCH":
                return ("OK", [b"1"])
            if cmd == "FETCH":
                return ("NO", [])  # non-OK status
            return ("OK", [b""])
        mock_conn.uid.side_effect = handler

        poller = EmailPoller(
            host="h", port=993, username="u", password="p",
            state_file=str(tmp_path / "ids.json"),
        )
        poller.connect()
        assert poller.fetch_unseen() == []

    def test_fetch_unseen_already_processed_skipped(self, mocker, tmp_path):
        """Messages whose Message-ID is already in processed set are skipped."""
        state_file = tmp_path / "ids.json"
        state_file.write_text(json.dumps(["<already@mail>"]))

        msg = email.message.EmailMessage()
        msg["Subject"] = "test"
        msg["Message-ID"] = "<already@mail>"
        msg.set_content("hello")

        mocker.patch("ssl.create_default_context", return_value=MagicMock())
        mock_class, mock_conn = _mock_imap(mocker, uid_list=[b"1"], raw_email=msg)

        poller = EmailPoller(
            host="h", port=993, username="u", password="p",
            state_file=str(state_file),
        )
        poller.connect()
        results = poller.fetch_unseen()
        assert results == []
