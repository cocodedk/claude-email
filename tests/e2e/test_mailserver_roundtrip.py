"""End-to-end proof that the e2e mail server speaks real SMTP and real IMAP.

Nothing here is mocked. A message is handed to a real SMTP server over a real
socket by one client, and a *second*, independently authenticated client pulls
it back over real IMAP. The body bytes must survive that trip unchanged.

This is the foundation slice: every later e2e slice builds on the fixture these
tests exercise, so the assertions are about the transport itself rather than
about any claude-email code.
"""
import email
import email.policy
import imaplib
import os
import smtplib

import pytest

# The body is built with explicit CRLF line endings because that is the wire
# format. It deliberately carries the things a transport is most likely to
# mangle: a header-shaped line inside the body, trailing whitespace, lines that
# require SMTP dot-stuffing, and non-ASCII in two scripts.
#
# There is no terminal CRLF. In SMTP the body's final line terminator is part of
# the ``\r\n.\r\n`` DATA terminator and is consumed by it, so a body ending in
# CRLF is *not* what any conformant server stores. Encoding that fact in the
# expected value keeps the comparison exact; stripping bytes off the retrieved
# message instead would quietly turn byte-identity into approximate identity.
BODY_LINES = (
    b"Subject-line-lookalike: not a header, part of the body.",
    b"Trailing spaces preserved:   ",
    b"Dot-stuffing check:",
    b".",
    b"..",
    b"Unicode passthrough: \xc3\xa6\xc3\xb8\xc3\xa5 \xd8\xb3\xd9\x84\xd8\xa7\xd9\x85",
    b"",
    b"end-of-body",
)
BODY_BYTES = b"\r\n".join(BODY_LINES)


def _nonce() -> str:
    """A per-run token, so a leftover message can never satisfy an assertion."""
    return os.urandom(16).hex()


def _build_message(nonce: str, sender: str, recipient: str) -> bytes:
    """Serialise an RFC 5322 message by hand, with wire line endings.

    Hand-rolled rather than built with ``email.message.EmailMessage`` so that
    the bytes put on the wire are exactly the bytes written here — a generator
    in the middle would make it ambiguous whether the transport or the library
    was responsible for any difference.
    """
    headers = (
        f"From: {sender}",
        f"To: {recipient}",
        f"Subject: e2e roundtrip {nonce}",
        f"X-E2E-Nonce: {nonce}",
        "MIME-Version: 1.0",
        'Content-Type: text/plain; charset="utf-8"',
        "Content-Transfer-Encoding: 8bit",
    )
    return "\r\n".join(headers).encode("utf-8") + b"\r\n\r\n" + BODY_BYTES


def _send(mailserver, sender, recipient_address: str, raw: bytes) -> dict:
    """Deliver ``raw`` over a real SMTP connection and return refused recipients."""
    with smtplib.SMTP(mailserver.host, mailserver.smtp_port, timeout=30) as smtp:
        smtp.login(sender.login, sender.password)
        return smtp.sendmail(sender.address, [recipient_address], raw)


def _fetch_by_nonce(imap: imaplib.IMAP4, nonce: str) -> bytes:
    """Return the raw RFC822 bytes of the message carrying ``nonce``."""
    status, _ = imap.select("INBOX")
    assert status == "OK", f"IMAP SELECT INBOX failed: {status}"
    status, data = imap.search(None, "HEADER", "X-E2E-Nonce", nonce)
    assert status == "OK", f"IMAP SEARCH failed: {status}"
    uids = data[0].split()
    assert len(uids) == 1, f"expected exactly one message for nonce {nonce}, got {uids!r}"
    status, fetched = imap.fetch(uids[0], "(RFC822)")
    assert status == "OK", f"IMAP FETCH failed: {status}"
    return fetched[0][1]


def test_smtp_to_imap_roundtrip_preserves_body_bytes(mailserver):
    """A body sent over real SMTP comes back over real IMAP byte-for-byte."""
    nonce = _nonce()
    sender = mailserver.accounts["sender"]
    recipient = mailserver.accounts["recipient"]

    # Client 1: real SMTP, real socket, nothing patched.
    refused = _send(mailserver, sender, recipient.address, _build_message(
        nonce, sender.address, recipient.address))
    assert refused == {}, f"SMTP refused recipients: {refused}"

    # Client 2: a separate connection, a separate account, a separate protocol.
    with mailserver.imap_client(recipient) as imap:
        retrieved = mailserver.wait_for(imap, nonce, _fetch_by_nonce)

    # Assert on the raw bytes first: everything after the header/body separator
    # must be the body and nothing but the body.
    assert retrieved.split(b"\r\n\r\n", 1)[1] == BODY_BYTES

    # And again through a parser, to confirm the message is well-formed rather
    # than merely containing the right substring.
    parsed = email.message_from_bytes(retrieved, policy=email.policy.default)
    assert parsed["X-E2E-Nonce"] == nonce
    assert parsed["Subject"] == f"e2e roundtrip {nonce}"
    assert parsed.get_content_type() == "text/plain"
    assert parsed.get_payload(decode=True) == BODY_BYTES


def test_delivery_is_routed_not_broadcast(mailserver):
    """The message reaches the addressed mailbox and no other.

    Without this, a server that copied every message into every mailbox would
    still pass the roundtrip test. This is what makes the roundtrip evidence of
    *delivery* rather than of storage.
    """
    nonce = _nonce()
    sender = mailserver.accounts["sender"]
    recipient = mailserver.accounts["recipient"]
    bystander = mailserver.accounts["bystander"]

    _send(mailserver, sender, recipient.address, _build_message(
        nonce, sender.address, recipient.address))

    with mailserver.imap_client(recipient) as imap:
        mailserver.wait_for(imap, nonce, _fetch_by_nonce)

    with mailserver.imap_client(bystander) as imap:
        status, _ = imap.select("INBOX")
        assert status == "OK"
        status, data = imap.search(None, "HEADER", "X-E2E-Nonce", nonce)
        assert status == "OK"
        assert data[0].split() == [], "message leaked into an unaddressed mailbox"


def test_both_protocols_enforce_authentication(mailserver):
    """Wrong credentials are refused on SMTP and on IMAP.

    The later slices assert things about *who* may send mail through this
    server; that only means anything if the server checks credentials at all.
    """
    recipient = mailserver.accounts["recipient"]

    with smtplib.SMTP(mailserver.host, mailserver.smtp_port, timeout=30) as smtp:
        with pytest.raises(smtplib.SMTPAuthenticationError):
            smtp.login(recipient.login, "not-the-password")

    imap = imaplib.IMAP4(mailserver.host, mailserver.imap_port)
    try:
        with pytest.raises(imaplib.IMAP4.error):
            imap.login(recipient.login, "not-the-password")
    finally:
        imap.shutdown()
