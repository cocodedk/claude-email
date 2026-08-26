"""The poller and the mailer speak TLS to GreenMail itself — nothing in between.

What this module exists to pin
-----------------------------
``src/poller.py`` and ``src/mailer.py`` build ``ssl.create_default_context()``
— certificate *and* hostname verification on, by repo invariant. GreenMail's
built-in keystore ships a certificate whose subject is ``CN=GreenMail
selfsigned Test Certificate`` with **no** subjectAltName, and CPython has
required a SAN for hostname verification since 3.7, so no verifying client can
ever accept it whatever CA it trusts.

The harness used to answer that by running a hand-rolled TLS terminator — a
protocol-blind byte pump that presented a SAN certificate of its own and
forwarded plaintext to GreenMail's cleartext port. It worked, and it cost two
crash fixes (a segfault on connection teardown, a two-thread OpenSSL race)
before it stopped killing the suite. A byte pump that has segfaulted twice has
no business sitting underneath the security assertions of every other e2e
module.

So GreenMail is now handed a keystore the harness generates: one throwaway
keypair whose SAN covers ``127.0.0.1`` and ``localhost``, bind-mounted into the
container and named by ``-Dgreenmail.tls.keystore.file``. The certificate the
poller verifies is served by **GreenMail's own** IMAPS and SMTPS listeners, and
the tests below assert that end to end: the certificate on the wire is
byte-identical to the CA file the children are given, the ports the children
are configured with are GreenMail's own published TLS ports, and an untrusted
client is still refused.

Why the absences are asserted directly
--------------------------------------
Two of the properties here are absences, and an absence cannot be observed by
watching a connection succeed: a terminator re-introduced tomorrow would make
every positive assertion in this file pass unchanged. They are therefore
asserted against the harness source itself — no TLS server socket in **any**
e2e module bar one, and the verified-context call still present in both
production transports. ``tests/e2e/test_failure_injection.py`` is the one
exemption: its fault injector *is* the failure under test — you cannot sever a
wire you are not standing on — and it reaches exactly one poller in one test.
"""
from __future__ import annotations

import email
import imaplib
import os
import re
import smtplib
import ssl
from pathlib import Path

import pytest

import _stack

HARNESS = Path(__file__).parent
REPO_ROOT = HARNESS.parents[1]

#: The *only* file allowed to hold a TLS server socket, and why. Written as an
#: exemption list rather than an allowlist of files to scan: a terminator added
#: tomorrow to a brand-new ``_tls_helper.py``, or to any other test module,
#: must trip this — which it would not if the scan only covered the two files
#: the harness happens to consist of today. ``test_failure_injection.py`` is
#: exempt because severing a live IMAP session before the server executes the
#: ``FETCH`` means standing on the wire; see that module's docstring. This file
#: is exempt because it necessarily spells the patterns it searches for.
TLS_SERVER_EXEMPT = ("test_failure_injection.py", Path(__file__).name)

#: Spellings of "wrap a socket as a TLS *server*". The second is a regex so
#: that whitespace around the ``=`` cannot evade it.
TLS_SERVER_PATTERNS = ("PROTOCOL_TLS_SERVER", r"server_side\s*=\s*True")

#: Name of the deleted harness class, assembled at runtime. The scan for it has
#: no exemptions — not even this file — so the literal must not appear here.
DELETED_CLASS = "Tls" + "Terminator"

#: Spellings of "stop verifying the peer". Split so that this file, which is
#: scanned by nothing, cannot be mistaken for a violation of its own rule.
WEAKENINGS = ("CERT_NONE", "check_hostname" + " = False",
              "check_hostname" + "=False", "_create_unverified_context")


def _verified(cafile: Path | None) -> ssl.SSLContext:
    """A context with verification fully on; ``None`` trusts only the system store."""
    ctx = ssl.create_default_context(cafile=str(cafile) if cafile else None)
    assert ctx.check_hostname is True
    assert ctx.verify_mode is ssl.CERT_REQUIRED
    return ctx


# ---------------------------------------------------------------------------
# The absences.
# ---------------------------------------------------------------------------

def test_no_tls_terminator_is_left_anywhere_in_the_e2e_tree():
    """No TLS *server* socket in any e2e module but the one exempted by name.

    The scan is over every ``*.py`` in the directory, present and future, with
    a two-name exemption list — not over a list of files to check. That
    direction matters: a terminator re-introduced tomorrow will almost
    certainly not carry the deleted class's name, and would live in whatever
    new module its author created.

    Fails if reverted: restore the class in ``_stack.py`` and both halves trip;
    add a server-side wrap to any non-exempt module and the sweep trips.
    """
    scanned = []
    for source in sorted(HARNESS.glob("*.py")):
        text = source.read_text()
        # No exemption for this one — the deleted name may appear nowhere.
        assert DELETED_CLASS not in text, f"{source.name} still names {DELETED_CLASS}"
        if source.name in TLS_SERVER_EXEMPT:
            continue
        scanned.append(source.name)
        for pattern in TLS_SERVER_PATTERNS:
            assert re.search(pattern, text) is None, (
                f"{source.name} terminates TLS ({pattern}) — only "
                f"{TLS_SERVER_EXEMPT[0]} may, and only for the severance it injects")

    # A scan that silently covered nothing would pass every assertion above.
    assert "_stack.py" in scanned and "conftest.py" in scanned, scanned
    assert not hasattr(_stack, DELETED_CLASS)


def test_both_production_transports_still_build_a_verified_context():
    """``create_default_context()`` in both, and no weakening in ``src/``.

    The acceptance criterion for this slice names these two call-sites, and it
    names them as an absence of weakening as much as a presence of the call.
    Both halves are asserted, and the negative half is asserted over all of
    ``src/`` rather than the two files, because the cheapest way to fake this
    slice would be a helper somewhere else that hands back a lax context.

    Fails if reverted: swap either transport to ``ssl._create_unverified_context``
    and the first assertion or the sweep trips.
    """
    for name in ("poller.py", "mailer.py"):
        text = (REPO_ROOT / "src" / name).read_text()
        assert "ssl.create_default_context()" in text, f"src/{name} lost its verified context"

    for source in sorted((REPO_ROOT / "src").rglob("*.py")):
        text = source.read_text()
        for weakening in WEAKENINGS:
            assert weakening not in text, f"src/{source.name} contains {weakening}"


# ---------------------------------------------------------------------------
# The certificate on the wire.
# ---------------------------------------------------------------------------

def test_greenmail_serves_the_harness_certificate_from_its_own_listener(mailserver):
    """The IMAPS peer certificate is byte-identical to the CA file, with the SAN.

    Byte-identity is what rules out an intermediary: the harness generates one
    self-signed certificate, hands the container its private key inside a
    keystore, and hands the children the public half as ``SSL_CERT_FILE``. If
    anything terminated TLS in front of GreenMail with a *different* key, the
    DER on the wire would differ even though the handshake still verified.

    Fails if reverted: unset ``greenmail.tls.keystore.file`` and GreenMail falls
    back to its bundled SAN-less certificate — the handshake fails outright.
    """
    with imaplib.IMAP4_SSL("127.0.0.1", mailserver.imaps_port,
                           ssl_context=_verified(mailserver.cafile)) as imap:
        on_the_wire = imap.sock.getpeercert(binary_form=True)
        described = imap.sock.getpeercert()

    expected = ssl.PEM_cert_to_DER_cert(Path(mailserver.cafile).read_text())
    assert on_the_wire == expected, "the certificate on the wire is not the harness CA"

    sans = set(described["subjectAltName"])
    assert ("IP Address", "127.0.0.1") in sans, sans
    assert ("DNS", "localhost") in sans, sans


def test_imaps_completes_a_verified_handshake_and_a_login(mailserver):
    """GreenMail's own IMAPS port serves a session a verifying client accepts."""
    account = mailserver.accounts["recipient"]
    with imaplib.IMAP4_SSL("127.0.0.1", mailserver.imaps_port,
                           ssl_context=_verified(mailserver.cafile)) as imap:
        assert imap.welcome.startswith(b"* OK")
        assert imap.login(account.login, account.password)[0] == "OK"
        assert imap.select("INBOX")[0] == "OK"


def test_smtps_completes_a_verified_handshake_and_a_login(mailserver):
    """GreenMail's own SMTPS port serves a session a verifying client accepts."""
    account = mailserver.accounts["sender"]
    with smtplib.SMTP_SSL("127.0.0.1", mailserver.smtps_port,
                          context=_verified(mailserver.cafile), timeout=30) as smtp:
        assert smtp.noop()[0] == 250
        assert smtp.login(account.login, account.password)[0] == 235


def test_an_untrusted_client_is_refused_on_both_transports(mailserver):
    """The negative control: without the CA, the same endpoints are rejected.

    This is what separates "verification is on" from "verification is
    configured". A harness that had quietly disabled hostname checking would
    pass every positive assertion above and fail here.
    """
    with pytest.raises(ssl.SSLCertVerificationError):
        imaplib.IMAP4_SSL("127.0.0.1", mailserver.imaps_port, ssl_context=_verified(None))

    with pytest.raises(ssl.SSLCertVerificationError):
        smtplib.SMTP_SSL("127.0.0.1", mailserver.smtps_port,
                         context=_verified(None), timeout=30)


def test_a_message_sent_over_smtps_is_read_back_over_imaps(mailserver):
    """One real message, in over verified SMTPS and out over verified IMAPS.

    Both directions of the production transport pair on one payload, so a
    listener that completed a handshake but could not carry a protocol would
    still be caught.
    """
    sender, recipient = mailserver.accounts["sender"], mailserver.accounts["recipient"]
    nonce = os.urandom(8).hex()
    raw = (f"From: {sender.address}\r\nTo: {recipient.address}\r\n"
           f"Subject: tls-direct {nonce}\r\n"
           f"Message-ID: <tls-direct-{nonce}@{mailserver.domain}>\r\n\r\n"
           f"tls direct body {nonce}\r\n")

    with smtplib.SMTP_SSL("127.0.0.1", mailserver.smtps_port,
                          context=_verified(mailserver.cafile), timeout=30) as smtp:
        smtp.login(sender.login, sender.password)
        smtp.sendmail(sender.address, [recipient.address], raw.encode())

    def _fetch(imap, wanted):
        status, data = imap.search(None, "SUBJECT", f'"tls-direct {wanted}"')
        assert status == "OK" and data and data[0], "not delivered yet"
        status, fetched = imap.fetch(data[0].split()[0], "(RFC822)")
        assert status == "OK", fetched
        return email.message_from_bytes(fetched[0][1])

    with imaplib.IMAP4_SSL("127.0.0.1", mailserver.imaps_port,
                           ssl_context=_verified(mailserver.cafile)) as imap:
        imap.login(recipient.login, recipient.password)
        imap.select("INBOX")
        message = mailserver.wait_for(imap, nonce, _fetch)

    assert message["Subject"] == f"tls-direct {nonce}"
    assert nonce in message.get_payload()


# ---------------------------------------------------------------------------
# What the live stack is actually pointed at.
# ---------------------------------------------------------------------------

def test_the_children_are_pointed_at_greenmails_own_tls_ports(stack, mailserver):
    """The poller's configured ports are the container's published TLS ports.

    Not "a port that answers TLS" — *these* ports, the ones docker publishes
    straight to GreenMail's ``imaps``/``smtps`` listeners. With a terminator in
    the path these were ephemeral loopback ports owned by the pytest process,
    so this is the assertion the deletion has to satisfy.
    """
    assert stack.imaps_port == mailserver.imaps_port
    assert stack.smtps_port == mailserver.smtps_port
    assert stack.env["IMAP_PORT"] == str(mailserver.imaps_port)
    assert stack.env["SMTP_PORT"] == str(mailserver.smtps_port)
    assert stack.env["IMAP_HOST"] == stack.env["SMTP_HOST"] == mailserver.host
    assert Path(stack.env["SSL_CERT_FILE"]) == Path(mailserver.cafile)

    # And the kernel agrees about the process that is really running.
    child = stack.poller.environ()
    assert child["IMAP_PORT"] == str(mailserver.imaps_port)
    assert child["SMTP_PORT"] == str(mailserver.smtps_port)
    assert child["SSL_CERT_FILE"] == str(mailserver.cafile)


def test_the_live_poller_logged_a_verified_session_to_that_port(stack, mailserver):
    """The real ``main.py`` reached GreenMail's IMAPS port and authenticated.

    ``src/poller.py`` logs this line only after ``IMAP4_SSL`` finished a
    verified handshake *and* ``login()`` returned OK, so it is evidence of a
    working verified session against the container itself rather than of a
    process that started and is retrying.
    """
    line = stack.poller.wait_for_output(r"IMAP connected to \S+ as \S+")
    assert f"{mailserver.host}:{mailserver.imaps_port}" in line
    assert line.endswith(stack.polled_account.login)
    assert stack.poller.is_running(), stack.poller.output()
