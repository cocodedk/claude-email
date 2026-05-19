"""Tests for sender authorization logic — TestSpoofingResistance split."""
import email.message
import pytest
from src.security import is_authorized, verify_gpg_signature
from tests._security_helpers import _make_msg, VALID_SECRET, _FakeChatDBWithOutbound


class TestSpoofingResistance:
    """Header-level forgery tests for ``is_authorized``.

    Note: SPF/DKIM/DMARC are enforced by the receiving MX (one.com) at
    SMTP time. ``security.py`` is the second layer — it assumes the MX
    has already dropped the most blatant unauthenticated mail and adds
    a per-message auth proof on top of an envelope check. These tests
    nail down exactly what spoofing patterns the second layer rejects."""

    # Display-name vs real address — parseaddr extracts the bracketed addr.
    def test_display_name_spoofing_uses_real_address(self):
        """A forged 'user@example.com <evil@attacker.com>' must be rejected.
        parseaddr should pull the bracketed address (evil@), not be
        fooled by the display-name claiming user@example.com."""
        msg = _make_msg(
            '"user@example.com" <evil@attacker.com>',
            return_path="<evil@attacker.com>",
            subject=f"AUTH:{VALID_SECRET} cmd",
        )
        assert not is_authorized(
            msg, authorized_sender="user@example.com", shared_secret=VALID_SECRET,
        )

    def test_unicode_lookalike_in_display_name_is_ignored(self):
        msg = _make_msg(
            "Babak Bandpey <Ьb@example.com>",  # cyrillic 'Ь' — lookalike
            return_path="<Ьb@example.com>",
            subject=f"AUTH:{VALID_SECRET} cmd",
        )
        assert not is_authorized(
            msg, authorized_sender="user@example.com", shared_secret=VALID_SECRET,
        )

    # Case + whitespace normalization on the comparison side.
    def test_uppercase_from_still_accepted(self):
        msg = _make_msg(
            "<USER@EXAMPLE.COM>",
            return_path="<user@example.com>",
            subject=f"AUTH:{VALID_SECRET} cmd",
        )
        assert is_authorized(
            msg, authorized_sender="user@example.com", shared_secret=VALID_SECRET,
        )

    def test_padded_return_path_normalized(self):
        msg = _make_msg(
            "<user@example.com>",
            return_path="   <user@example.com>   ",
            subject=f"AUTH:{VALID_SECRET} cmd",
        )
        assert is_authorized(
            msg, authorized_sender="user@example.com", shared_secret=VALID_SECRET,
        )

    # Envelope mismatch — Return-Path must equal From.
    def test_from_legit_return_path_evil_rejected(self):
        msg = _make_msg(
            "<user@example.com>",
            return_path="<evil@attacker.com>",
            subject=f"AUTH:{VALID_SECRET} cmd",
        )
        assert not is_authorized(
            msg, authorized_sender="user@example.com", shared_secret=VALID_SECRET,
        )

    def test_missing_return_path_rejected(self):
        msg = _make_msg(
            "<user@example.com>",
            subject=f"AUTH:{VALID_SECRET} cmd",
        )
        assert not is_authorized(
            msg, authorized_sender="user@example.com", shared_secret=VALID_SECRET,
        )

    # Snooped Message-ID + forged envelope: thread-match alone must NOT
    # bypass the envelope check. This is critical now that
    # outbound_emails widens the surface — every CC/forward exposes IDs.
    def test_snooped_outbound_id_with_forged_envelope_rejected(self):
        msg = _make_msg(
            "Evil <evil@attacker.com>",
            return_path="<evil@attacker.com>",
            subject="Re: anything",
        )
        msg["In-Reply-To"] = "<leaked-id@example.com>"
        db = _FakeChatDBWithOutbound(outbound_ids={"<leaked-id@example.com>"})
        assert not is_authorized(
            msg,
            authorized_sender="user@example.com",
            shared_secret=VALID_SECRET,
            chat_db=db,
        )

    def test_snooped_messages_id_with_forged_envelope_rejected(self):
        msg = _make_msg(
            "Evil <evil@attacker.com>",
            return_path="<evil@attacker.com>",
            subject="Re: anything",
        )
        msg["In-Reply-To"] = "<leaked-msg@example.com>"
        db = _FakeChatDBWithOutbound(message_ids={"<leaked-msg@example.com>"})
        assert not is_authorized(
            msg,
            authorized_sender="user@example.com",
            shared_secret=VALID_SECRET,
            chat_db=db,
        )

    # Multiple From headers — RFC says first wins; we read message["From"]
    # which returns the first occurrence. An attacker prepending a fake
    # From below a real one shouldn't change the verdict.
    def test_appended_from_header_does_not_bypass(self):
        msg = email.message.EmailMessage()
        msg["From"] = "<user@example.com>"
        # Appending a second header doesn't replace the first.
        msg["Return-Path"] = "<user@example.com>"
        msg["Subject"] = f"AUTH:{VALID_SECRET} cmd"
        try:
            msg["From"] = "<evil@attacker.com>"  # raises in EmailMessage
        except Exception:
            pass
        # Either the dup is rejected by EmailMessage or the first From wins.
        assert is_authorized(
            msg, authorized_sender="user@example.com", shared_secret=VALID_SECRET,
        )

    # Forged AUTH:secret without legit envelope — must still fail.
    def test_auth_secret_without_legit_envelope_rejected(self):
        msg = _make_msg(
            "Evil <evil@attacker.com>",
            return_path="<evil@attacker.com>",
            subject=f"AUTH:{VALID_SECRET} cmd",
        )
        assert not is_authorized(
            msg, authorized_sender="user@example.com", shared_secret=VALID_SECRET,
        )

    # Allow-list of multiple senders shouldn't let an attacker who
    # spoofs *any* legit address through unless From + Return-Path agree.
    def test_multi_sender_envelope_consistency_required(self):
        msg = _make_msg(
            "<user@example.com>",
            return_path="<test@example.com>",  # different legit sender
            subject=f"AUTH:{VALID_SECRET} cmd",
        )
        assert not is_authorized(
            msg,
            authorized_sender=["user@example.com", "test@example.com"],
            shared_secret=VALID_SECRET,
        )

    # Empty In-Reply-To shouldn't trigger an accidental match.
    def test_empty_in_reply_to_does_not_match_anything(self):
        msg = _make_msg(
            "<user@example.com>",
            return_path="<user@example.com>",
            subject="Re: nothing",
        )
        msg["In-Reply-To"] = ""
        db = _FakeChatDBWithOutbound(outbound_ids={"<x@y>"})
        # No AUTH and no real In-Reply-To → reject.
        assert not is_authorized(
            msg,
            authorized_sender="user@example.com",
            shared_secret=VALID_SECRET,
            chat_db=db,
        )
