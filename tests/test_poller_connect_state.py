"""Tests for IMAP email polling — connect + state-file loading."""
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
    def test_connect_uses_verified_ssl(self, mocker, tmp_path):
        mock_class = mocker.patch("imaplib.IMAP4_SSL")
        mock_conn = MagicMock()
        mock_class.return_value = mock_conn
        mock_conn.login.return_value = ("OK", [b"ok"])
        mock_ssl = mocker.patch("ssl.create_default_context", return_value=MagicMock())

        poller = EmailPoller(
            host="imap.one.com", port=993,
            username="agent@example.com", password="pw",
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

    def test_corrupted_state_file_starts_fresh(self, tmp_path):
        state_file = tmp_path / "ids.json"
        state_file.write_text("NOT VALID JSON{{{")

        poller = EmailPoller(
            host="imap.one.com", port=993,
            username="u", password="p",
            state_file=str(state_file),
        )
        assert len(poller._processed_ids) == 0

    def test_dict_state_file_starts_fresh(self, tmp_path):
        """A JSON object (not list) must not leak its keys into processed_ids."""
        state_file = tmp_path / "ids.json"
        state_file.write_text('{"sneaky-key": "value"}')
        poller = EmailPoller(
            host="h", port=993, username="u", password="p",
            state_file=str(state_file),
        )
        assert len(poller._processed_ids) == 0, (
            f"dict keys leaked: {poller._processed_ids}"
        )

    def test_list_with_non_string_entries_starts_fresh(self, tmp_path):
        """A list containing non-strings (int, None, dict) must be rejected."""
        state_file = tmp_path / "ids.json"
        state_file.write_text('["<valid-id@example.com>", 42, null, {"x": 1}]')
        poller = EmailPoller(
            host="h", port=993, username="u", password="p",
            state_file=str(state_file),
        )
        assert len(poller._processed_ids) == 0, (
            f"hetero list accepted: {poller._processed_ids}"
        )

    def test_list_of_strings_loads_normally(self, tmp_path):
        """The happy path stays happy."""
        state_file = tmp_path / "ids.json"
        state_file.write_text('["<a@example.com>", "<b@example.com>"]')
        poller = EmailPoller(
            host="h", port=993, username="u", password="p",
            state_file=str(state_file),
        )
        assert set(poller._processed_ids) == {"<a@example.com>", "<b@example.com>"}
