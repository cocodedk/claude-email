"""Tests for sender authorization logic."""
import email.message
import pytest
from src.security import is_authorized, verify_gpg_signature


def _make_msg(from_header: str, return_path: str = "", subject: str = "") -> email.message.Message:
    msg = email.message.EmailMessage()
    msg["From"] = from_header
    if return_path:
        msg["Return-Path"] = return_path
    if subject:
        msg["Subject"] = subject
    return msg


VALID_SECRET = "supersecret"


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


class _FakeChatDB:
    """Minimal stand-in for ChatDB.find_message_by_email_id."""

    def __init__(self, known_ids):
        self._known = set(known_ids)

    def find_message_by_email_id(self, email_message_id):
        if email_message_id in self._known:
            return {"id": 1, "from_name": "agent-x", "email_message_id": email_message_id}
        return None


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
        msg["In-Reply-To"] = "<known-chat-msg@cocode.dk>"
        db = _FakeChatDB(known_ids={"<known-chat-msg@cocode.dk>"})
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
        msg["In-Reply-To"] = "<known-chat-msg@cocode.dk>"
        db = _FakeChatDB(known_ids={"<known-chat-msg@cocode.dk>"})
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


class TestVerifyGpgSignature:
    def test_valid_signature_matching_fingerprint_passes(self, mocker):
        mock_gpg_cls = mocker.patch("gnupg.GPG")
        mock_gpg = mock_gpg_cls.return_value
        mock_result = mocker.MagicMock()
        mock_result.valid = True
        mock_result.fingerprint = VALID_FINGERPRINT
        mock_gpg.verify.return_value = mock_result

        msg = _make_gpg_msg()
        assert verify_gpg_signature(msg, authorized_fingerprint=VALID_FINGERPRINT)

    def test_invalid_signature_rejected(self, mocker):
        mock_gpg_cls = mocker.patch("gnupg.GPG")
        mock_gpg = mock_gpg_cls.return_value
        mock_result = mocker.MagicMock()
        mock_result.valid = False
        mock_result.fingerprint = VALID_FINGERPRINT
        mock_gpg.verify.return_value = mock_result

        msg = _make_gpg_msg()
        assert not verify_gpg_signature(msg, authorized_fingerprint=VALID_FINGERPRINT)

    def test_wrong_fingerprint_rejected(self, mocker):
        mock_gpg_cls = mocker.patch("gnupg.GPG")
        mock_gpg = mock_gpg_cls.return_value
        mock_result = mocker.MagicMock()
        mock_result.valid = True
        mock_result.fingerprint = "DIFFERENT000FINGERPRINT000000000000000000"
        mock_gpg.verify.return_value = mock_result

        msg = _make_gpg_msg()
        assert not verify_gpg_signature(msg, authorized_fingerprint=VALID_FINGERPRINT)

    def test_no_pgp_block_rejected(self, mocker):
        mocker.patch("gnupg.GPG")
        msg = _make_gpg_msg(signed=False)
        assert not verify_gpg_signature(msg, authorized_fingerprint=VALID_FINGERPRINT)

    def test_pgp_mime_valid_signature_passes(self, mocker):
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from email.mime.application import MIMEApplication

        mock_gpg_cls = mocker.patch("gnupg.GPG")
        mock_gpg = mock_gpg_cls.return_value
        mock_result = mocker.MagicMock()
        mock_result.valid = True
        mock_result.fingerprint = VALID_FINGERPRINT
        mock_gpg.verify_data.return_value = mock_result

        # Build a PGP/MIME multipart/signed message
        msg = MIMEMultipart("signed", protocol="application/pgp-signature")
        msg["From"] = "user@example.com"
        msg["Return-Path"] = "<user@example.com>"
        msg.attach(MIMEText("run tests", "plain"))
        msg.attach(MIMEApplication(b"fakesigbytes", "pgp-signature"))

        assert verify_gpg_signature(msg, authorized_fingerprint=VALID_FINGERPRINT)

    def test_pgp_mime_passes_filesystem_path_to_verify_data(self, mocker):
        """python-gnupg's verify_data(sig_filename, data) takes a path, not
        raw bytes — it passes sig_filename straight to ``gpg --verify`` as a
        CLI arg. Passing bytes makes gpg treat the bytes-as-string as a
        non-existent file path and verification always fails ('verify:
        file not found'). Pin the fix: first arg must be a str path whose
        file contains the detached signature bytes."""
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from email.mime.application import MIMEApplication
        import os

        mock_gpg_cls = mocker.patch("gnupg.GPG")
        mock_gpg = mock_gpg_cls.return_value
        mock_result = mocker.MagicMock()
        mock_result.valid = True
        mock_result.fingerprint = VALID_FINGERPRINT
        captured = {}

        def fake_verify(sig_filename, data):
            # Must be a str path, and the file must exist with the sig bytes
            # in it while verify_data is being called.
            assert isinstance(sig_filename, str), (
                f"verify_data first arg must be a str path, got "
                f"{type(sig_filename).__name__}: {sig_filename!r}"
            )
            assert os.path.isfile(sig_filename), (
                f"sig_filename {sig_filename!r} must be an existing file"
            )
            with open(sig_filename, "rb") as f:
                captured["sig_bytes"] = f.read()
            captured["data"] = data
            return mock_result

        mock_gpg.verify_data.side_effect = fake_verify

        msg = MIMEMultipart("signed", protocol="application/pgp-signature")
        msg["From"] = "user@example.com"
        msg["Return-Path"] = "<user@example.com>"
        msg.attach(MIMEText("run tests", "plain"))
        msg.attach(MIMEApplication(b"fakesigbytes", "pgp-signature"))

        assert verify_gpg_signature(msg, authorized_fingerprint=VALID_FINGERPRINT)
        assert captured["sig_bytes"] == b"fakesigbytes"


class TestIsAuthorizedWithGpg:
    def test_gpg_mode_bypasses_shared_secret(self, mocker):
        """When gpg_fingerprint is set, shared secret in subject is not required."""
        mock_verify = mocker.patch("src.security.verify_gpg_signature", return_value=True)
        msg = _make_gpg_msg()
        # No AUTH: prefix in subject — GPG mode should not care
        assert is_authorized(
            msg,
            authorized_sender="user@example.com",
            shared_secret="irrelevant",
            gpg_fingerprint=VALID_FINGERPRINT,
        )
        mock_verify.assert_called_once()

    def test_gpg_mode_rejects_invalid_signature(self, mocker):
        mocker.patch("src.security.verify_gpg_signature", return_value=False)
        msg = _make_gpg_msg()
        assert not is_authorized(
            msg,
            authorized_sender="user@example.com",
            shared_secret="irrelevant",
            gpg_fingerprint=VALID_FINGERPRINT,
        )

    def test_gpg_mode_still_checks_from_header(self, mocker):
        mocker.patch("src.security.verify_gpg_signature", return_value=True)
        msg = _make_gpg_msg(from_addr="hacker@evil.com")
        assert not is_authorized(
            msg,
            authorized_sender="user@example.com",
            shared_secret="irrelevant",
            gpg_fingerprint=VALID_FINGERPRINT,
        )


class TestPgpMimeMissingParts:
    def test_missing_signature_part_rejected(self, mocker):
        """PGP/MIME message without an application/pgp-signature part returns False."""
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        mocker.patch("gnupg.GPG")

        msg = MIMEMultipart("signed", protocol="application/pgp-signature")
        msg["From"] = "user@example.com"
        msg["Return-Path"] = "<user@example.com>"
        msg.attach(MIMEText("run tests", "plain"))
        # No pgp-signature part attached

        assert not verify_gpg_signature(msg, authorized_fingerprint=VALID_FINGERPRINT)

    def test_missing_message_part_rejected(self, mocker):
        """PGP/MIME message with only a signature part and no body returns False."""
        from email.mime.multipart import MIMEMultipart
        from email.mime.application import MIMEApplication

        mocker.patch("gnupg.GPG")

        msg = MIMEMultipart("signed", protocol="application/pgp-signature")
        msg["From"] = "user@example.com"
        msg["Return-Path"] = "<user@example.com>"
        msg.attach(MIMEApplication(b"fakesigbytes", "pgp-signature"))
        # No text body part — only the signature

        assert not verify_gpg_signature(msg, authorized_fingerprint=VALID_FINGERPRINT)


class TestInlinePgpMultipart:
    def test_multipart_with_inline_pgp_passes(self, mocker):
        """Inline PGP inside a multipart message (e.g. multipart/alternative)."""
        mock_gpg_cls = mocker.patch("gnupg.GPG")
        mock_gpg = mock_gpg_cls.return_value
        mock_result = mocker.MagicMock()
        mock_result.valid = True
        mock_result.fingerprint = VALID_FINGERPRINT
        mock_gpg.verify.return_value = mock_result

        msg = email.message.EmailMessage()
        msg["From"] = "user@example.com"
        msg["Return-Path"] = "<user@example.com>"
        pgp_body = (
            "-----BEGIN PGP SIGNED MESSAGE-----\n"
            "Hash: SHA256\n\nrun tests\n\n"
            "-----BEGIN PGP SIGNATURE-----\n\nfakesigdata\n"
            "-----END PGP SIGNATURE-----\n"
        )
        msg.set_content(pgp_body)
        msg.add_alternative("<html><body>run tests</body></html>", subtype="html")

        assert verify_gpg_signature(msg, authorized_fingerprint=VALID_FINGERPRINT)


class TestGpgNullFingerprint:
    def test_null_fingerprint_rejected(self, mocker):
        """If GPG verification returns no fingerprint, reject."""
        mock_gpg_cls = mocker.patch("gnupg.GPG")
        mock_gpg = mock_gpg_cls.return_value
        mock_result = mocker.MagicMock()
        mock_result.valid = True
        mock_result.fingerprint = None
        mock_gpg.verify.return_value = mock_result

        msg = _make_gpg_msg()
        assert not verify_gpg_signature(msg, authorized_fingerprint=VALID_FINGERPRINT)


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
        msg["In-Reply-To"] = "<cli-result@cocode.dk>"
        db = _FakeChatDBWithOutbound(outbound_ids={"<cli-result@cocode.dk>"})
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
        msg["In-Reply-To"] = "<cli-result@cocode.dk>"
        db = _FakeChatDBWithOutbound(outbound_ids={"<cli-result@cocode.dk>"})
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
        msg["In-Reply-To"] = "<relay-msg@cocode.dk>"
        db = _FakeChatDBWithOutbound(message_ids={"<relay-msg@cocode.dk>"})
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
            "Babak Bandpey <Ьb@cocode.dk>",  # cyrillic 'Ь' — lookalike
            return_path="<Ьb@cocode.dk>",
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
        msg["In-Reply-To"] = "<leaked-id@cocode.dk>"
        db = _FakeChatDBWithOutbound(outbound_ids={"<leaked-id@cocode.dk>"})
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
        msg["In-Reply-To"] = "<leaked-msg@cocode.dk>"
        db = _FakeChatDBWithOutbound(message_ids={"<leaked-msg@cocode.dk>"})
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
