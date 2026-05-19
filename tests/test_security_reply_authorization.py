"""Tests for sender authorization logic — TestReplyAuthorization split."""
import email.message
import pytest
from src.security import is_authorized, verify_gpg_signature
from tests._security_helpers import _make_msg, VALID_SECRET, _FakeChatDB


class TestReplyAuthorization:
    """Chat replies that come back in-thread should be accepted without AUTH prefix.

    Mail clients don't reproduce the AUTH:<secret> subject prefix on reply, so
    requiring it on every inbound email breaks the chat-relay flow. These tests
    cover the two compensating paths added to is_authorized:
      1. In-Reply-To header matches a Message-ID we issued (known chat thread).
      2. The AUTH:<secret> token appears in the body (quoted reply propagation
         or a user who manually types it in the reply body).
    """

    def test_in_reply_to_matching_known_chat_id_accepts_without_auth(self):
        msg = _make_msg(
            "Babak <user@example.com>",
            return_path="<user@example.com>",
            subject="Re: [master-fixer] message",
        )
        msg["In-Reply-To"] = "<known-chat-msg@example.com>"
        db = _FakeChatDB(known_ids={"<known-chat-msg@example.com>"})
        assert is_authorized(
            msg,
            authorized_sender="user@example.com",
            shared_secret=VALID_SECRET,
            chat_db=db,
        )

    def test_in_reply_to_unknown_id_still_requires_auth(self):
        msg = _make_msg(
            "Babak <user@example.com>",
            return_path="<user@example.com>",
            subject="Re: random subject nothing to see here",
        )
        msg["In-Reply-To"] = "<never-seen-before@example.com>"
        db = _FakeChatDB(known_ids=set())
        assert not is_authorized(
            msg,
            authorized_sender="user@example.com",
            shared_secret=VALID_SECRET,
            chat_db=db,
        )

    def test_in_reply_to_bypass_still_requires_envelope(self):
        """An attacker with a known Message-ID must still pass From + Return-Path."""
        msg = _make_msg(
            "Evil <evil@attacker.com>",
            return_path="<evil@attacker.com>",
            subject="Re: [master-fixer] message",
        )
        msg["In-Reply-To"] = "<known-chat-msg@example.com>"
        db = _FakeChatDB(known_ids={"<known-chat-msg@example.com>"})
        assert not is_authorized(
            msg,
            authorized_sender="user@example.com",
            shared_secret=VALID_SECRET,
            chat_db=db,
        )

    def test_body_containing_auth_secret_accepted_plain_text(self):
        msg = email.message.EmailMessage()
        msg["From"] = "Babak <user@example.com>"
        msg["Return-Path"] = "<user@example.com>"
        msg["Subject"] = "Re: [master-fixer] message"
        msg.set_content(
            "my reply text\n\n> From: ...\n> Subject: AUTH:"
            + VALID_SECRET
            + " original command\n",
        )
        assert is_authorized(
            msg, authorized_sender="user@example.com", shared_secret=VALID_SECRET,
        )

    def test_body_without_auth_and_no_chat_db_rejected(self):
        msg = email.message.EmailMessage()
        msg["From"] = "Babak <user@example.com>"
        msg["Return-Path"] = "<user@example.com>"
        msg["Subject"] = "Re: [master-fixer] message"
        msg.set_content("just a reply, no secret, no nothing")
        assert not is_authorized(
            msg, authorized_sender="user@example.com", shared_secret=VALID_SECRET,
        )

    def test_body_auth_in_html_part_accepted(self):
        """Mail clients often send HTML-only replies — secret in HTML should count."""
        msg = email.message.EmailMessage()
        msg["From"] = "Babak <user@example.com>"
        msg["Return-Path"] = "<user@example.com>"
        msg["Subject"] = "Re: [master-fixer] message"
        msg.set_content("plain fallback")
        msg.add_alternative(
            f"<html><body><p>hello</p><blockquote>Subject: AUTH:{VALID_SECRET} orig</blockquote></body></html>",
            subtype="html",
        )
        assert is_authorized(
            msg, authorized_sender="user@example.com", shared_secret=VALID_SECRET,
        )

    def test_chat_db_none_keeps_standard_behavior(self):
        """Passing chat_db=None should behave exactly like not passing it."""
        msg = _make_msg(
            "Babak <user@example.com>",
            return_path="<user@example.com>",
            subject=f"AUTH:{VALID_SECRET} do thing",
        )
        assert is_authorized(
            msg,
            authorized_sender="user@example.com",
            shared_secret=VALID_SECRET,
            chat_db=None,
        )

    def test_multipart_empty_payload_skipped(self):
        """Cover the `if not payload: continue` branch in _extract_body_text.

        Build a multipart/mixed with a text/plain part whose decoded payload
        is empty bytes (falsy) — the extractor must skip it without crashing
        and still evaluate the remaining parts.
        """
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        msg = MIMEMultipart("mixed")
        msg["From"] = "Babak <user@example.com>"
        msg["Return-Path"] = "<user@example.com>"
        msg["Subject"] = "Re: [master-fixer] message"
        # An empty text part whose get_payload(decode=True) returns b""
        empty_part = MIMEText("", "plain")
        msg.attach(empty_part)
        # A real part that carries the secret
        good_part = MIMEText(
            f"quoted block: AUTH:{VALID_SECRET} original command", "plain",
        )
        msg.attach(good_part)
        assert is_authorized(
            msg, authorized_sender="user@example.com", shared_secret=VALID_SECRET,
        )

    def test_empty_shared_secret_rejects_auth_prefix(self):
        """If shared_secret is empty, a bare 'AUTH:' prefix must NOT pass.

        Defense-in-depth: main.py refuses to start with no secret and no
        GPG, but is_authorized must also reject bare 'AUTH:' directly.
        """
        msg = _make_msg(
            "Babak <user@example.com>",
            return_path="<user@example.com>",
            subject="AUTH: do something",
        )
        assert not is_authorized(
            msg, authorized_sender="user@example.com", shared_secret="",
        )

    def test_single_part_html_body_secret_accepted(self):
        """Cover the non-multipart HTML body branch in _extract_body_text."""
        from email.message import EmailMessage
        msg = EmailMessage()
        msg["From"] = "Babak <user@example.com>"
        msg["Return-Path"] = "<user@example.com>"
        msg["Subject"] = "Re: [master-fixer] message"
        msg.set_content(
            f"<p>hello AUTH:{VALID_SECRET} world</p>", subtype="html",
        )
        assert not msg.is_multipart()  # sanity: exercises the non-multipart branch
        assert is_authorized(
            msg, authorized_sender="user@example.com", shared_secret=VALID_SECRET,
        )
