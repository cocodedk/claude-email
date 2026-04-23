"""GPG signature verification for inbound email.

Extracted from ``src/security.py`` to keep that file under the 200-line
limit and to isolate the PGP/MIME detached-signature handling, which has
a subtle python-gnupg contract that's easy to get wrong: ``verify_data``
takes a *filename*, not raw signature bytes.
"""
from __future__ import annotations

import email.message
import logging
import tempfile

logger = logging.getLogger(__name__)


def verify_gpg_signature(
    message: email.message.Message,
    authorized_fingerprint: str,
    gpg_home: str | None = None,
) -> bool:
    """Verify a GPG signature on an email message.

    Handles both inline PGP (-----BEGIN PGP SIGNED MESSAGE-----) and
    PGP/MIME (multipart/signed with application/pgp-signature part).

    Returns True only if the signature is valid AND was made by the key
    with the given fingerprint.
    """
    import gnupg

    gpg = gnupg.GPG(gnupghome=gpg_home)
    fingerprint = authorized_fingerprint.upper()

    content_type = message.get_content_type()

    if content_type == "multipart/signed":
        # PGP/MIME: find the text part and the signature part
        sig_bytes = None
        msg_bytes = None
        for part in message.get_payload():
            if part.get_content_type() == "application/pgp-signature":
                sig_bytes = part.get_payload(decode=True)
            else:
                msg_bytes = part.as_bytes()
        if sig_bytes is None or msg_bytes is None:
            logger.warning("GPG/MIME: could not find both message and signature parts")
            return False
        # python-gnupg's verify_data(sig_filename, data) passes the first
        # arg straight to `gpg --verify` as a filesystem path. Passing
        # raw bytes yields "verify: file not found" in every real call.
        # Materialise the detached signature to a temp file and hand gpg
        # the path — the with-block cleans up after return.
        with tempfile.NamedTemporaryFile(suffix=".sig") as sig_file:
            sig_file.write(sig_bytes)
            sig_file.flush()
            result = gpg.verify_data(sig_file.name, msg_bytes)
    else:
        # Inline PGP: look for PGP block in the plain-text body
        body = ""
        if message.is_multipart():
            for part in message.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        body = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                        break
        else:
            payload = message.get_payload(decode=True)
            if payload:
                body = payload.decode(message.get_content_charset() or "utf-8", errors="replace")

        if "-----BEGIN PGP SIGNED MESSAGE-----" not in body:
            logger.warning("GPG inline: no PGP signed message block found")
            return False
        result = gpg.verify(body)

    if not result.valid:
        logger.warning("GPG: signature verification failed")
        return False
    if not result.fingerprint or result.fingerprint.upper() != fingerprint:
        logger.warning(
            "GPG: signature fingerprint %r does not match authorized %r",
            result.fingerprint,
            fingerprint,
        )
        return False

    return True
