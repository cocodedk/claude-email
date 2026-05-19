"""Tests for sender authorization logic — TestIsAuthorized split."""
import email.message
import pytest
from src.security import is_authorized, verify_gpg_signature
from tests._security_helpers import _make_msg, VALID_SECRET


class TestIsAuthorized:
    def test_valid_sender_passes(self):
        msg = _make_msg(
            "Babak <user@example.com>",
            return_path="<user@example.com>",
            subject=f"AUTH:{VALID_SECRET} do something",
        )
        assert is_authorized(msg, authorized_sender="user@example.com", shared_secret=VALID_SECRET)

    def test_wrong_from_rejected(self):
        msg = _make_msg(
            "hacker@evil.com",
            return_path="<user@example.com>",
            subject=f"AUTH:{VALID_SECRET} do something",
        )
        assert not is_authorized(msg, authorized_sender="user@example.com", shared_secret=VALID_SECRET)

    def test_wrong_return_path_rejected(self):
        msg = _make_msg(
            "user@example.com",
            return_path="<hacker@evil.com>",
            subject=f"AUTH:{VALID_SECRET} do something",
        )
        assert not is_authorized(msg, authorized_sender="user@example.com", shared_secret=VALID_SECRET)

    def test_missing_return_path_rejected(self):
        msg = _make_msg("user@example.com", subject=f"AUTH:{VALID_SECRET} do something")
        assert not is_authorized(msg, authorized_sender="user@example.com", shared_secret=VALID_SECRET)

    def test_wrong_secret_rejected(self):
        msg = _make_msg(
            "user@example.com",
            return_path="<user@example.com>",
            subject="AUTH:wrongsecret do something",
        )
        assert not is_authorized(msg, authorized_sender="user@example.com", shared_secret=VALID_SECRET)

    def test_missing_secret_in_subject_rejected(self):
        msg = _make_msg(
            "user@example.com",
            return_path="<user@example.com>",
            subject="do something without auth",
        )
        assert not is_authorized(msg, authorized_sender="user@example.com", shared_secret=VALID_SECRET)

    def test_from_contains_trick_rejected(self):
        """'Contains' check is unsafe — must do exact domain match."""
        msg = _make_msg(
            "user@example.com.evil.com",
            return_path="<user@example.com>",
            subject=f"AUTH:{VALID_SECRET} do something",
        )
        assert not is_authorized(msg, authorized_sender="user@example.com", shared_secret=VALID_SECRET)

    def test_missing_from_rejected(self):
        msg = email.message.EmailMessage()
        msg["Return-Path"] = "<user@example.com>"
        msg["Subject"] = f"AUTH:{VALID_SECRET} cmd"
        assert not is_authorized(msg, authorized_sender="user@example.com", shared_secret=VALID_SECRET)

    def test_reply_subject_with_re_prefix_passes(self):
        """Replying to a reply produces 'Re: AUTH:secret' — should still be accepted."""
        msg = _make_msg(
            "Babak <user@example.com>",
            return_path="<user@example.com>",
            subject=f"Re: AUTH:{VALID_SECRET} do something",
        )
        assert is_authorized(msg, authorized_sender="user@example.com", shared_secret=VALID_SECRET)

    def test_multiple_re_prefixes_pass(self):
        """Re: Re: AUTH:secret should also be accepted."""
        msg = _make_msg(
            "Babak <user@example.com>",
            return_path="<user@example.com>",
            subject=f"Re: Re: AUTH:{VALID_SECRET} do something",
        )
        assert is_authorized(msg, authorized_sender="user@example.com", shared_secret=VALID_SECRET)

    def test_fwd_prefix_passes(self):
        """Forwarded subjects with AUTH:secret must auth — the website
        advertises Fwd-prefix support."""
        msg = _make_msg(
            "Babak <user@example.com>",
            return_path="<user@example.com>",
            subject=f"Fwd: AUTH:{VALID_SECRET} run the build",
        )
        assert is_authorized(msg, authorized_sender="user@example.com", shared_secret=VALID_SECRET)

    def test_fw_prefix_passes(self):
        msg = _make_msg(
            "Babak <user@example.com>",
            return_path="<user@example.com>",
            subject=f"FW: AUTH:{VALID_SECRET} ping",
        )
        assert is_authorized(msg, authorized_sender="user@example.com", shared_secret=VALID_SECRET)

    def test_mixed_re_fwd_prefixes_pass(self):
        msg = _make_msg(
            "Babak <user@example.com>",
            return_path="<user@example.com>",
            subject=f"Re: Fwd: AUTH:{VALID_SECRET} status",
        )
        assert is_authorized(msg, authorized_sender="user@example.com", shared_secret=VALID_SECRET)
