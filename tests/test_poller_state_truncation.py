"""Tests for IMAP email polling — _MAX_PROCESSED_IDS truncation."""
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
    def test_load_state_truncates_oversized_file(self, tmp_path):
        """If the state file has more than _MAX_PROCESSED_IDS entries, only the last N are kept (line 51)."""
        import src.poller as poller_module
        original_max = poller_module._MAX_PROCESSED_IDS
        poller_module._MAX_PROCESSED_IDS = 5
        try:
            # Write 10 IDs — exceeds the limit of 5
            ids = [f"<msg{i}@mail>" for i in range(10)]
            state_file = tmp_path / "ids.json"
            state_file.write_text(json.dumps(ids))

            poller = EmailPoller(
                host="h", port=993, username="u", password="p",
                state_file=str(state_file),
            )
            # Should contain only the last 5
            assert len(poller._processed_ids) == 5
            assert "<msg9@mail>" in poller._processed_ids
            assert "<msg0@mail>" not in poller._processed_ids
        finally:
            poller_module._MAX_PROCESSED_IDS = original_max

    def test_save_state_truncates_preserving_insertion_order(self, mocker, tmp_path):
        """When _processed_ids exceeds _MAX, truncation must drop the OLDEST,
        not arbitrary entries, and the freshly-added ID must survive.

        Prior bug (CodeRabbit Major on PR #10): _processed_ids was a set, so
        list(self._processed_ids)[-N:] in _save_state had no insertion-order
        semantics — the newly-appended Message-ID could be dropped if it
        happened to fall outside the arbitrary tail of set-to-list conversion,
        silently breaking replay protection.
        """
        import src.poller as poller_module
        original_max = poller_module._MAX_PROCESSED_IDS
        poller_module._MAX_PROCESSED_IDS = 5
        try:
            mocker.patch("ssl.create_default_context", return_value=MagicMock())
            _mock_imap(mocker)

            state_file = tmp_path / "ids.json"
            # Seed ordered state via the state file itself
            state_file.write_text(json.dumps([f"<msg{i}@mail>" for i in range(10)]))
            poller = EmailPoller(
                host="h", port=993, username="u", password="p",
                state_file=str(state_file),
            )
            poller.connect()

            poller.mark_processed("1", "<msg10@mail>")

            saved = json.loads(state_file.read_text())
            # With MAX=5, the 5 most-recently-inserted survive: msg6..msg10.
            # Crucially, the freshly marked <msg10@mail> must be present.
            assert saved == [f"<msg{i}@mail>" for i in range(6, 11)]
            assert "<msg10@mail>" in saved
        finally:
            poller_module._MAX_PROCESSED_IDS = original_max

    def test_save_state_preserves_newest_even_if_processed_ids_was_full(
        self, mocker, tmp_path,
    ):
        """Direct demonstration: newly added id survives even when at cap."""
        import src.poller as poller_module
        original_max = poller_module._MAX_PROCESSED_IDS
        poller_module._MAX_PROCESSED_IDS = 3
        try:
            mocker.patch("ssl.create_default_context", return_value=MagicMock())
            _mock_imap(mocker)
            state_file = tmp_path / "ids.json"
            state_file.write_text(json.dumps(["<a>", "<b>", "<c>"]))
            poller = EmailPoller(
                host="h", port=993, username="u", password="p",
                state_file=str(state_file),
            )
            poller.connect()
            poller.mark_processed("1", "<d>")
            saved = json.loads(state_file.read_text())
            assert saved == ["<b>", "<c>", "<d>"], (
                f"newest-first invariant broken: {saved}"
            )
        finally:
            poller_module._MAX_PROCESSED_IDS = original_max
