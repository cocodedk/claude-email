"""Shared fixtures/helpers for ``test_reply_recipient_*`` modules.

Underscore prefix → pytest's default ``test_*`` collection skips this
file. Imported explicitly by the split test modules.

Original module docstring (kept verbatim for context — these helpers
were extracted from ``tests/test_reply_recipient.py``):

    Replies must address the sender that actually sent the inbound, not the
    canonical/first AUTHORIZED_SENDER. With multiple senders configured (the
    multi-user / alias case), routing every reply to the canonical means alias
    senders can write but never receive — exactly the bug surfaced by the
    2026-05-02 Android-app smoke test.

    These tests pin the contract for every reply path:

      - ``_send_json_reply``      (envelope ack/error/result for JSON inbound)
      - ``send_threaded_reply``   (CLI [Running]/[Result], @agent acks, meta)
      - ``recipient_for_message`` (async result emails relayed by chat_relay)

    Tasks remember the actual inbound sender (``origin_from``) so the relay,
    which fires later without the inbound message in hand, can still address
    the right inbox.
"""
import email.message


def _inbound(from_addr: str, msg_id: str = "<m@x>") -> email.message.EmailMessage:
    m = email.message.EmailMessage()
    m["From"] = from_addr
    m["Return-Path"] = f"<{from_addr}>"
    m["Subject"] = "ping"
    m["Message-ID"] = msg_id
    m.set_content("body")
    return m


def _multi_sender_config() -> dict:
    """Canonical + one alias, both authorized."""
    return {
        "smtp_host": "smtp.example.com", "smtp_port": 465,
        "username": "claude@example.com", "password": "pw",
        "authorized_sender": "bb@example.com",
        "authorized_senders": ["bb@example.com", "alias@example.com"],
        "email_domain": "example.com",
    }
