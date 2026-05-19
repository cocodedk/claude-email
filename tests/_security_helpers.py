"""Shared fixtures and stand-ins for the security test split.

Filename starts with an underscore so pytest will not try to collect it as
a test module. Imported by the ``tests/test_security_*.py`` files.
"""
import email.message


def _make_msg(from_header: str, return_path: str = "", subject: str = "") -> email.message.Message:
    msg = email.message.EmailMessage()
    msg["From"] = from_header
    if return_path:
        msg["Return-Path"] = return_path
    if subject:
        msg["Subject"] = subject
    return msg


VALID_SECRET = "supersecret"


class _FakeChatDB:
    """Minimal stand-in for ChatDB.find_message_by_email_id."""

    def __init__(self, known_ids):
        self._known = set(known_ids)

    def find_message_by_email_id(self, email_message_id):
        if email_message_id in self._known:
            return {"id": 1, "from_name": "agent-x", "email_message_id": email_message_id}
        return None


VALID_FINGERPRINT = "AABBCCDDEEFF00112233445566778899AABBCCDD"


def _make_gpg_msg(from_addr: str = "user@example.com", signed: bool = True) -> email.message.EmailMessage:
    msg = email.message.EmailMessage()
    msg["From"] = from_addr
    msg["Return-Path"] = f"<{from_addr}>"
    msg["Subject"] = "run tests"
    if signed:
        msg.set_content(
            "-----BEGIN PGP SIGNED MESSAGE-----\n"
            "Hash: SHA256\n\n"
            "run tests\n\n"
            "-----BEGIN PGP SIGNATURE-----\n\n"
            "fakesigdata\n"
            "-----END PGP SIGNATURE-----\n"
        )
    else:
        msg.set_content("run tests")
    return msg


class _FakeChatDBWithOutbound:
    """Stand-in supporting both lookup paths used by is_authorized."""

    def __init__(self, *, message_ids=(), outbound_ids=()):
        self._messages = set(message_ids)
        self._outbound = set(outbound_ids)

    def find_message_by_email_id(self, email_message_id):
        if email_message_id in self._messages:
            return {"id": 1, "from_name": "agent-x", "email_message_id": email_message_id}
        return None

    def find_outbound_email(self, email_message_id):
        if email_message_id in self._outbound:
            return {"email_message_id": email_message_id, "kind": "ack"}
        return None
