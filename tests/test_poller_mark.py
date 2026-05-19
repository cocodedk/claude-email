"""Tests for IMAP email polling — mark_processed behavior."""
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
    def test_mark_processed_no_connection(self, tmp_path):
        poller = EmailPoller(
            host="h", port=993, username="u", password="p",
            state_file=str(tmp_path / "ids.json"),
        )
        poller.mark_processed("1", "<test@mail>")  # should not raise

    def test_mark_processed_store_failure(self, mocker, tmp_path):
        mocker.patch("ssl.create_default_context", return_value=MagicMock())
        mock_class, mock_conn = _mock_imap(mocker)
        mock_conn.uid.side_effect = Exception("store failed")

        poller = EmailPoller(
            host="h", port=993, username="u", password="p",
            state_file=str(tmp_path / "ids.json"),
        )
        poller.connect()
        poller.mark_processed("1", "<test@mail>")  # should not raise
        # Message ID still recorded despite STORE failure
        assert "<test@mail>" in poller._processed_ids

    def test_mark_processed_saves_state(self, mocker, tmp_path):
        mocker.patch("ssl.create_default_context", return_value=MagicMock())
        mock_class, mock_conn = _mock_imap(mocker)

        state_file = tmp_path / "ids.json"
        poller = EmailPoller(
            host="h", port=993, username="u", password="p",
            state_file=str(state_file),
        )
        poller.connect()
        poller.mark_processed("1", "<saved@mail>")

        saved = json.loads(state_file.read_text())
        assert "<saved@mail>" in saved

    def test_mark_processed_empty_message_id(self, mocker, tmp_path):
        mocker.patch("ssl.create_default_context", return_value=MagicMock())
        mock_class, mock_conn = _mock_imap(mocker)

        state_file = tmp_path / "ids.json"
        poller = EmailPoller(
            host="h", port=993, username="u", password="p",
            state_file=str(state_file),
        )
        poller.connect()
        poller.mark_processed("1", "")  # empty message_id — should not save
        assert not state_file.exists()
