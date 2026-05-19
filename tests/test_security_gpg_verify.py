"""Tests for sender authorization logic — TestVerifyGpgSignature split."""
import email.message
import pytest
from src.security import is_authorized, verify_gpg_signature
from tests._security_helpers import _make_gpg_msg, VALID_FINGERPRINT


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
