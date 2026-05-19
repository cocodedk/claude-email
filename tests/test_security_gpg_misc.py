"""Tests for sender authorization logic — GPG mode + PGP/MIME edge cases."""
import email.message
from src.security import is_authorized, verify_gpg_signature
from tests._security_helpers import _make_gpg_msg, VALID_FINGERPRINT


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
