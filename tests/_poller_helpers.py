"""Shared helpers for poller tests (underscore-prefixed so pytest skips)."""
from unittest.mock import MagicMock


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
