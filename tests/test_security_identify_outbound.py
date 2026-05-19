"""Tests for sender authorization logic — identify_sender + outbound thread match."""
import email.message
import pytest
from src.security import is_authorized, verify_gpg_signature
from tests._security_helpers import _make_msg, VALID_SECRET, _FakeChatDBWithOutbound


class TestIdentifySender:
    def test_empty_senders_returns_none(self):
        import email.message
        from src.security import identify_sender
        msg = email.message.Message()
        msg["From"] = "bb@x"
        msg["Return-Path"] = "<bb@x>"
        assert identify_sender(msg, []) is None

    def test_whitespace_only_senders_returns_none(self):
        import email.message
        from src.security import identify_sender
        msg = email.message.Message()
        msg["From"] = "bb@x"
        msg["Return-Path"] = "<bb@x>"
        assert identify_sender(msg, ["", "   "]) is None

    def test_multi_sender_match_returns_matching(self):
        import email.message
        from src.security import identify_sender
        msg = email.message.Message()
        msg["From"] = "Test <test@example.com>"
        msg["Return-Path"] = "<test@example.com>"
        assert identify_sender(msg, ["bb@x", "test@example.com"]) == "test@example.com"

    def test_multi_sender_no_match_returns_none(self):
        import email.message
        from src.security import identify_sender
        msg = email.message.Message()
        msg["From"] = "<evil@x>"
        msg["Return-Path"] = "<evil@x>"
        assert identify_sender(msg, ["bb@x", "test@x"]) is None


class TestOutboundEmailsThreadMatch:
    """Replies that thread to a non-relay outbound (CLI [Result], JSON
    envelope reply, @agent ACK) must auth via the outbound_emails lookup
    even when messages.email_message_id misses. This was the path that
    silently rejected the user's chrome-extension thread replies."""

    def test_in_reply_to_matching_outbound_email_accepts(self):
        msg = _make_msg(
            "Babak <user@example.com>",
            return_path="<user@example.com>",
            subject="Re: [Result] do the thing",
        )
        msg["In-Reply-To"] = "<cli-result@example.com>"
        db = _FakeChatDBWithOutbound(outbound_ids={"<cli-result@example.com>"})
        assert is_authorized(
            msg,
            authorized_sender="user@example.com",
            shared_secret=VALID_SECRET,
            chat_db=db,
        )

    def test_outbound_match_still_requires_envelope(self):
        msg = _make_msg(
            "Evil <evil@x>",
            return_path="<evil@x>",
            subject="Re: [Result] something",
        )
        msg["In-Reply-To"] = "<cli-result@example.com>"
        db = _FakeChatDBWithOutbound(outbound_ids={"<cli-result@example.com>"})
        assert not is_authorized(
            msg,
            authorized_sender="user@example.com",
            shared_secret=VALID_SECRET,
            chat_db=db,
        )

    def test_messages_lookup_still_works_when_outbound_misses(self):
        """The pre-existing relay path keeps its behavior — a reply that
        threads to messages.email_message_id is accepted regardless of
        whether outbound_emails has a row."""
        msg = _make_msg(
            "Babak <user@example.com>",
            return_path="<user@example.com>",
            subject="Re: relay",
        )
        msg["In-Reply-To"] = "<relay-msg@example.com>"
        db = _FakeChatDBWithOutbound(message_ids={"<relay-msg@example.com>"})
        assert is_authorized(
            msg,
            authorized_sender="user@example.com",
            shared_secret=VALID_SECRET,
            chat_db=db,
        )

    def test_unknown_in_reply_to_still_requires_auth(self):
        msg = _make_msg(
            "Babak <user@example.com>",
            return_path="<user@example.com>",
            subject="Re: random",
        )
        msg["In-Reply-To"] = "<never-issued@x>"
        db = _FakeChatDBWithOutbound()
        assert not is_authorized(
            msg,
            authorized_sender="user@example.com",
            shared_secret=VALID_SECRET,
            chat_db=db,
        )
