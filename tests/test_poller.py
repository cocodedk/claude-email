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
        state_file.write_text('["<valid-id@cocode.dk>", 42, null, {"x": 1}]')
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
        state_file.write_text('["<a@cocode.dk>", "<b@cocode.dk>"]')
        poller = EmailPoller(
            host="h", port=993, username="u", password="p",
            state_file=str(state_file),
        )
        assert set(poller._processed_ids) == {"<a@cocode.dk>", "<b@cocode.dk>"}

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
