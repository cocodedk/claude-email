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
            "Babak <bb@cocode.dk>",
            return_path="<bb@cocode.dk>",
            subject=f"AUTH:{VALID_SECRET} do something",
        )
        assert is_authorized(msg, authorized_sender="bb@cocode.dk", shared_secret=VALID_SECRET)

    def test_wrong_from_rejected(self):
        msg = _make_msg(
            "hacker@evil.com",
            return_path="<bb@cocode.dk>",
            subject=f"AUTH:{VALID_SECRET} do something",
        )
        assert not is_authorized(msg, authorized_sender="bb@cocode.dk", shared_secret=VALID_SECRET)

    def test_wrong_return_path_rejected(self):
        msg = _make_msg(
            "bb@cocode.dk",
            return_path="<hacker@evil.com>",
            subject=f"AUTH:{VALID_SECRET} do something",
        )
        assert not is_authorized(msg, authorized_sender="bb@cocode.dk", shared_secret=VALID_SECRET)

    def test_missing_return_path_rejected(self):
        msg = _make_msg("bb@cocode.dk", subject=f"AUTH:{VALID_SECRET} do something")
        assert not is_authorized(msg, authorized_sender="bb@cocode.dk", shared_secret=VALID_SECRET)

    def test_wrong_secret_rejected(self):
        msg = _make_msg(
            "bb@cocode.dk",
            return_path="<bb@cocode.dk>",
            subject="AUTH:wrongsecret do something",
        )
        assert not is_authorized(msg, authorized_sender="bb@cocode.dk", shared_secret=VALID_SECRET)

    def test_missing_secret_in_subject_rejected(self):
        msg = _make_msg(
            "bb@cocode.dk",
            return_path="<bb@cocode.dk>",
            subject="do something without auth",
        )
        assert not is_authorized(msg, authorized_sender="bb@cocode.dk", shared_secret=VALID_SECRET)

    def test_from_contains_trick_rejected(self):
        """'Contains' check is unsafe — must do exact domain match."""
        msg = _make_msg(
            "bb@cocode.dk.evil.com",
            return_path="<bb@cocode.dk>",
            subject=f"AUTH:{VALID_SECRET} do something",
        )
        assert not is_authorized(msg, authorized_sender="bb@cocode.dk", shared_secret=VALID_SECRET)

    def test_missing_from_rejected(self):
        msg = email.message.EmailMessage()
        msg["Return-Path"] = "<bb@cocode.dk>"
        msg["Subject"] = f"AUTH:{VALID_SECRET} cmd"
        assert not is_authorized(msg, authorized_sender="bb@cocode.dk", shared_secret=VALID_SECRET)

    def test_reply_subject_with_re_prefix_passes(self):
        """Replying to a reply produces 'Re: AUTH:secret' — should still be accepted."""
        msg = _make_msg(
            "Babak <bb@cocode.dk>",
            return_path="<bb@cocode.dk>",
            subject=f"Re: AUTH:{VALID_SECRET} do something",
        )
        assert is_authorized(msg, authorized_sender="bb@cocode.dk", shared_secret=VALID_SECRET)

    def test_multiple_re_prefixes_pass(self):
        """Re: Re: AUTH:secret should also be accepted."""
        msg = _make_msg(
            "Babak <bb@cocode.dk>",
            return_path="<bb@cocode.dk>",
            subject=f"Re: Re: AUTH:{VALID_SECRET} do something",
        )
        assert is_authorized(msg, authorized_sender="bb@cocode.dk", shared_secret=VALID_SECRET)


VALID_FINGERPRINT = "AABBCCDDEEFF00112233445566778899AABBCCDD"


def _make_gpg_msg(from_addr: str = "bb@cocode.dk", signed: bool = True) -> email.message.EmailMessage:
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
        msg["From"] = "bb@cocode.dk"
        msg["Return-Path"] = "<bb@cocode.dk>"
        msg.attach(MIMEText("run tests", "plain"))
        msg.attach(MIMEApplication(b"fakesigbytes", "pgp-signature"))

        assert verify_gpg_signature(msg, authorized_fingerprint=VALID_FINGERPRINT)


class TestIsAuthorizedWithGpg:
    def test_gpg_mode_bypasses_shared_secret(self, mocker):
        """When gpg_fingerprint is set, shared secret in subject is not required."""
        mock_verify = mocker.patch("src.security.verify_gpg_signature", return_value=True)
        msg = _make_gpg_msg()
        # No AUTH: prefix in subject — GPG mode should not care
        assert is_authorized(
            msg,
            authorized_sender="bb@cocode.dk",
            shared_secret="irrelevant",
            gpg_fingerprint=VALID_FINGERPRINT,
        )
        mock_verify.assert_called_once()

    def test_gpg_mode_rejects_invalid_signature(self, mocker):
        mocker.patch("src.security.verify_gpg_signature", return_value=False)
        msg = _make_gpg_msg()
        assert not is_authorized(
            msg,
            authorized_sender="bb@cocode.dk",
            shared_secret="irrelevant",
            gpg_fingerprint=VALID_FINGERPRINT,
        )

    def test_gpg_mode_still_checks_from_header(self, mocker):
        mocker.patch("src.security.verify_gpg_signature", return_value=True)
        msg = _make_gpg_msg(from_addr="hacker@evil.com")
        assert not is_authorized(
            msg,
            authorized_sender="bb@cocode.dk",
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
        msg["From"] = "bb@cocode.dk"
        msg["Return-Path"] = "<bb@cocode.dk>"
        msg.attach(MIMEText("run tests", "plain"))
        # No pgp-signature part attached

        assert not verify_gpg_signature(msg, authorized_fingerprint=VALID_FINGERPRINT)

    def test_missing_message_part_rejected(self, mocker):
        """PGP/MIME message with only a signature part and no body returns False."""
        from email.mime.multipart import MIMEMultipart
        from email.mime.application import MIMEApplication

        mocker.patch("gnupg.GPG")

        msg = MIMEMultipart("signed", protocol="application/pgp-signature")
        msg["From"] = "bb@cocode.dk"
        msg["Return-Path"] = "<bb@cocode.dk>"
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
        msg["From"] = "bb@cocode.dk"
        msg["Return-Path"] = "<bb@cocode.dk>"
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
