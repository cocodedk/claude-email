"""Tests for IMAP email polling — content-bound replay refusal.

The Message-ID store makes redelivery idempotent; these pin the second key,
which makes a *captured credential* single-use even when the envelope around
it has been rewritten. `tests/e2e/test_replay.py` proves the same property
against a real mail server and a real signature.
"""
import email
import json
from unittest.mock import MagicMock

from src.poller import EmailPoller
from src.replay_guard import replay_key
from tests._poller_helpers import _mock_imap

SIGNATURE = (
    "-----BEGIN PGP SIGNATURE-----\n\n"
    "aGVsbG8gd29ybGQgc2lnbmF0dXJlIGJ5dGVz\n"
    "-----END PGP SIGNATURE-----"
)


def _signed(message_id: str):
    raw = (
        f"From: s@x\r\nTo: r@x\r\nSubject: cmd\r\nMessage-ID: {message_id}\r\n"
        "MIME-Version: 1.0\r\n"
        'Content-Type: multipart/signed; protocol="application/pgp-signature";'
        ' micalg=pgp-sha256; boundary="B"\r\n\r\n'
        "--B\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nrun it\r\n"
        "--B\r\nContent-Type: application/pgp-signature\r\n\r\n"
        f"{SIGNATURE}\r\n--B--\r\n"
    )
    return email.message_from_string(raw)


def _poller(mocker, tmp_path, message, state=None):
    mocker.patch("ssl.create_default_context", return_value=MagicMock())
    _mock_imap(mocker, uid_list=[b"1"], raw_email=message)
    state_file = tmp_path / "ids.json"
    if state is not None:
        state_file.write_text(json.dumps(state))
    poller = EmailPoller(host="h", port=993, username="u", password="p",
                         state_file=str(state_file))
    poller.connect()
    return poller, state_file


class TestContentReplay:
    def test_same_signature_under_a_fresh_message_id_is_skipped(self, mocker, tmp_path):
        """The vulnerability this module exists to close.

        The Message-ID has never been seen, so the first guard passes it; the
        signature has, so the second one refuses it.
        """
        message = _signed("<fresh@mail>")
        poller, _ = _poller(mocker, tmp_path, message,
                            state=[replay_key(_signed("<original@mail>"))])
        assert poller.fetch_unseen() == []

    def test_a_different_signature_is_still_delivered(self, mocker, tmp_path):
        """The guard refuses replays, not traffic."""
        message = _signed("<fresh@mail>")
        poller, _ = _poller(mocker, tmp_path, message, state=["sig:" + "0" * 64])
        assert [uid for uid, _ in poller.fetch_unseen()] == ["1"]

    def test_processing_a_signed_message_records_both_keys(self, mocker, tmp_path):
        message = _signed("<first@mail>")
        poller, state_file = _poller(mocker, tmp_path, message)
        (uid, fetched), = poller.fetch_unseen()
        poller.mark_processed(uid, fetched.get("Message-ID", "").strip())
        assert json.loads(state_file.read_text()) == [
            "<first@mail>", replay_key(message)]

    def test_an_unsigned_message_records_only_its_message_id(self, mocker, tmp_path):
        """No content-bound credential means no opinion — not a shared key.

        Were the empty key stored, the *next* unsigned message would collide
        with this one and the mailbox would jam after a single plain mail.
        """
        raw = ("From: s@x\r\nTo: r@x\r\nSubject: cmd\r\nMessage-ID: <plain@mail>\r\n"
               "Content-Type: text/plain\r\n\r\nhello\r\n")
        message = email.message_from_string(raw)
        poller, state_file = _poller(mocker, tmp_path, message)
        (uid, _), = poller.fetch_unseen()
        poller.mark_processed(uid, "<plain@mail>")
        assert json.loads(state_file.read_text()) == ["<plain@mail>"]

    def test_a_batch_abandoned_before_marking_does_not_accumulate(self, mocker, tmp_path):
        """Every fetch resets the pending map, so a shutdown cannot leak it.

        The two fetches return *different* uids, so a map that merely appended
        would end up with both — which is exactly the leak this pins.
        """
        mocker.patch("ssl.create_default_context", return_value=MagicMock())
        _mock_imap(mocker, uid_list=[b"7"], raw_email=_signed("<first@mail>"))
        poller = EmailPoller(host="h", port=993, username="u", password="p",
                             state_file=str(tmp_path / "ids.json"))
        poller.connect()
        poller.fetch_unseen()
        assert list(poller._pending_keys) == ["7"]

        _mock_imap(mocker, uid_list=[b"9"], raw_email=_signed("<second@mail>"))
        poller.connect()
        poller.fetch_unseen()
        assert list(poller._pending_keys) == ["9"]

    def test_two_copies_in_one_batch_yield_only_one_message(self, mocker, tmp_path):
        """The persisted store is written after the batch, so the batch self-checks.

        `main.py` calls `mark_processed` only once it has finished with a
        message, so two copies of the same captured signature delivered inside
        a single poll interval would both be returned — and both executed —
        if `fetch_unseen` consulted the store alone.
        """
        mocker.patch("ssl.create_default_context", return_value=MagicMock())
        message = _signed("<a@mail>")
        replayed = _signed("<b@mail>")
        mock_class = mocker.patch("imaplib.IMAP4_SSL")
        conn = MagicMock()
        mock_class.return_value = conn
        conn.login.return_value = ("OK", [b"ok"])
        conn.select.return_value = ("OK", [b"2"])
        bodies = {b"1": message.as_bytes(), b"2": replayed.as_bytes()}

        def handler(cmd, *args):
            if cmd == "SEARCH":
                return ("OK", [b"1 2"])
            if cmd == "FETCH":
                return ("OK", [(b"x (RFC822 ...)", bodies[args[0]])])
            return ("OK", [b""])
        conn.uid.side_effect = handler

        poller = EmailPoller(host="h", port=993, username="u", password="p",
                             state_file=str(tmp_path / "ids.json"))
        poller.connect()
        assert [uid for uid, _ in poller.fetch_unseen()] == ["1"]

    def test_a_duplicate_message_id_in_one_batch_is_skipped(self, mocker, tmp_path):
        """Same guard on the other key: one delivery, not two."""
        mocker.patch("ssl.create_default_context", return_value=MagicMock())
        raw = ("From: s@x\r\nTo: r@x\r\nMessage-ID: <dup@mail>\r\n"
               "Content-Type: text/plain\r\n\r\nhello\r\n").encode()
        mock_class = mocker.patch("imaplib.IMAP4_SSL")
        conn = MagicMock()
        mock_class.return_value = conn
        conn.login.return_value = ("OK", [b"ok"])
        conn.select.return_value = ("OK", [b"2"])

        def handler(cmd, *args):
            if cmd == "SEARCH":
                return ("OK", [b"1 2"])
            if cmd == "FETCH":
                return ("OK", [(b"x (RFC822 ...)", raw)])
            return ("OK", [b""])
        conn.uid.side_effect = handler

        poller = EmailPoller(host="h", port=993, username="u", password="p",
                             state_file=str(tmp_path / "ids.json"))
        poller.connect()
        assert [uid for uid, _ in poller.fetch_unseen()] == ["1"]
