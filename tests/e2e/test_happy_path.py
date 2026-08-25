"""One real command, end to end, observed only from the outside.

A GPG-signed mail is handed to the real SMTP server by a real client. The real
``main.py`` polls it over real IMAP, verifies the real signature with the real
``gpg`` binary, routes it, forks the configured ``claude`` binary, and mails the
output back over real SMTP. The assertions here read only what an outside
observer can read: the reply mail pulled back over IMAP, the file the executed
process left on disk, and the rows the bus wrote to SQLite. Nothing reaches into
the poller process — no ``Child`` handle, no log scraping, no patching.

The designed oracle
-------------------
Before the mail is sent, the test computes what the effect *must* be: the exact
prompt string the CLI has to receive, its byte length, and its SHA-256. That
prediction is derived from the bytes put on the wire using the standard library
alone. The system's output is then compared against the prediction. Nothing is
asserted against a value the system itself produced.

On the CLI stand-in
-------------------
The ``claude`` CLI is *outside* the system under test — it is the third-party
program claude-email shells out to, and its output is non-deterministic, costs
money, and needs network. So this test replaces the harness's refusing stub
(``_stack.write_cli_stub``, whose docstring anticipates exactly this) with a
deterministic real executable. It is not a mock of anything claude-email owns:
it is reached by a real ``fork``/``exec`` from the real ``src/executor.py``,
with a real argv, and its real stdout travels back through the real mailer.
Every component in scope — IMAP, GPG, routing, subprocess, SMTP, SQLite — runs
unmodified.

Why each test fails if the implementation is reverted: see the individual
docstrings. In short, every assertion is downstream of production code that
must run correctly for the observable to exist at all.
"""
from __future__ import annotations

import dataclasses
import email
import email.message
import email.utils
import hashlib
import imaplib
import json
import os
import smtplib
import sqlite3
from pathlib import Path

import pytest

#: A fixed MIME boundary keeps the signed bytes easy to reason about.
BOUNDARY = "e2e-happy-path-boundary"
#: A poll cycle is 1s and the CLI timeout is 30s; allow for a cold first cycle.
REPLY_TIMEOUT = 180.0

#: The stand-in for the ``claude`` binary. It reports a pure function of the
#: prompt it was handed and leaves a receipt on disk, so the same run is
#: observable through two independent surfaces.
STUB_SOURCE = '''#!/usr/bin/env python3
"""Deterministic stand-in for the claude CLI, installed by test_happy_path."""
import hashlib
import json
import os
import sys

argv = sys.argv[1:]
if "--print" not in argv:
    sys.stderr.write("e2e stub: no --print in argv: %r\\n" % (argv,))
    raise SystemExit(3)
prompt = argv[argv.index("--print") + 1]
raw = prompt.encode("utf-8")
digest = hashlib.sha256(raw).hexdigest()
with open(__RECEIPT__, "w", encoding="utf-8") as handle:
    json.dump({"argv": argv, "prompt": prompt, "cwd": os.getcwd(),
               "sha256": digest, "length": len(raw)}, handle)
sys.stdout.write(
    "E2E-STUB-OUTPUT\\n"
    "prompt-sha256: " + digest + "\\n"
    "prompt-bytes: " + str(len(raw)) + "\\n"
    "cwd: " + os.getcwd() + "\\n"
)
'''


@dataclasses.dataclass(frozen=True)
class Prediction:
    """What the test computed the effect must be, before running anything."""

    command: str
    sha256: str
    length: int
    cwd: str
    argv: list


@dataclasses.dataclass(frozen=True)
class Observed:
    """What an outside observer can see afterwards."""

    message_id: str
    subject: str
    predicted: Prediction
    running: email.message.Message
    result: email.message.Message
    receipt_path: Path
    db_path: Path


def _command_text(nonce: str) -> str:
    """A two-line command with wire line endings and non-ASCII.

    CRLF and UTF-8 are in here on purpose: the prompt has to survive SMTP,
    IMAP, MIME parsing and ``execve`` unaltered, and the receipt comparison is
    byte-exact, so any normalisation anywhere on that path fails the test.
    """
    return (
        f"e2e happy path {nonce}: report the designed oracle for this prompt."
        "\r\n"
        "second line, non-ASCII: æøå سلام"
    )


def _inner_part(command: str) -> bytes:
    """The signed MIME part. The trailing CRLF belongs to the boundary."""
    return (
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"Content-Transfer-Encoding: 8bit\r\n"
        b"\r\n" + command.encode("utf-8") + b"\r\n"
    )


def _assemble(headers: bytes, inner: bytes, signature: bytes) -> bytes:
    """Serialise the RFC 3156 ``multipart/signed`` message by hand."""
    marker = BOUNDARY.encode()
    return (
        headers
        + b"\r\n--" + marker + b"\r\n" + inner
        + b"--" + marker + b"\r\n"
        b'Content-Type: application/pgp-signature; name="signature.asc"\r\n'
        b"Content-Description: OpenPGP digital signature\r\n"
        b"\r\n" + signature + b"\r\n"
        b"--" + marker + b"--\r\n"
    )


def _headers(sender: str, recipient: str, subject: str, message_id: str) -> bytes:
    lines = (
        f"From: {sender}",
        f"To: {recipient}",
        f"Subject: {subject}",
        f"Message-ID: {message_id}",
        f"Date: {email.utils.formatdate(localtime=False)}",
        "MIME-Version: 1.0",
        'Content-Type: multipart/signed; protocol="application/pgp-signature";'
        f' micalg=pgp-sha256; boundary="{BOUNDARY}"',
    )
    return "\r\n".join(lines).encode("utf-8") + b"\r\n"


def _sign_target(headers: bytes, inner: bytes) -> bytes:
    """The bytes a verifier will hand to gpg.

    ``src/gpg_verify.py`` verifies ``part.as_bytes()`` of the *parsed* message,
    and the stdlib parser normalises the part's line endings on reserialisation.
    Rather than guess at that transformation, run it: parse a copy of the very
    message about to be sent (the signature part cannot affect the first part's
    serialisation) and sign whatever comes out.
    """
    placeholder = _assemble(headers, inner, b"placeholder")
    return email.message_from_bytes(placeholder).get_payload()[0].as_bytes()


def _install_stub(path: Path, receipt: Path) -> bytes:
    """Swap in the deterministic CLI, returning the bytes it displaced."""
    original = path.read_bytes()
    path.write_text(STUB_SOURCE.replace("__RECEIPT__", repr(str(receipt))))
    path.chmod(0o700)
    return original


def _send(mailserver, account, recipient: str, raw: bytes) -> dict:
    """Deliver over a real SMTP connection; GreenMail sets Return-Path."""
    with smtplib.SMTP(mailserver.host, mailserver.smtp_port, timeout=30) as smtp:
        smtp.login(account.login, account.password)
        return smtp.sendmail(account.address, [recipient], raw)


def _rfc822_bytes(fetched) -> bytes:
    """Pull the single RFC822 literal out of a FETCH response.

    A plain ``FETCH ... (RFC822)`` implicitly sets ``\\Seen`` — verified against
    this very server — so any other client reading the mailbox makes it emit
    untagged ``* n FETCH (FLAGS (\\Seen))`` lines, and a concurrent delivery makes
    it emit ``* n EXISTS``. The server may interleave either into *this* fetch's
    response, and imaplib returns them in the same list as bare ``bytes`` rather
    than ``(descriptor, literal)`` tuples. Indexing ``[0][1]`` blindly then hands
    back one byte of a flag update — an ``int`` — instead of the message.
    """
    literals = [part[1] for part in fetched
                if isinstance(part, tuple) and isinstance(part[1], (bytes, bytearray))]
    assert len(literals) == 1, f"expected one RFC822 literal, got {fetched!r}"
    return literals[0]


def _tagged_replies(imap: imaplib.IMAP4, message_id: str) -> dict:
    """Return ``{tag: message}`` for every reply threaded on ``message_id``.

    Raises ``AssertionError`` until both the ack and the result have arrived,
    which is the contract ``MailServer.wait_for`` polls on.
    """
    status, _ = imap.select("INBOX")
    assert status == "OK", f"IMAP SELECT INBOX failed: {status}"
    status, data = imap.search(None, "ALL")
    assert status == "OK", f"IMAP SEARCH failed: {status}"
    found = {}
    for uid in data[0].split():
        status, fetched = imap.fetch(uid, "(RFC822)")
        assert status == "OK", f"IMAP FETCH failed: {status}"
        parsed = email.message_from_bytes(_rfc822_bytes(fetched))
        if parsed.get("In-Reply-To", "").strip() != message_id:
            continue
        subject = parsed.get("Subject", "")
        for tag in ("Running", "Result"):
            if f"[{tag}]" in subject:
                found[tag] = parsed
    assert set(found) == {"Running", "Result"}, f"only got {sorted(found)}"
    return found


def _body_text(message: email.message.Message) -> str:
    payload = message.get_payload(decode=True)
    assert payload is not None, "reply had no decodable payload"
    return payload.decode(message.get_content_charset() or "utf-8")


@pytest.fixture(scope="module")
def observed(stack) -> Observed:
    """Run one real command through the booted stack, exactly once.

    Module-scoped because the round trip costs a poll cycle plus SMTP delivery
    and every assertion below looks at a different surface of the same run.
    """
    nonce = os.urandom(12).hex()
    command = _command_text(nonce)
    subject = f"e2e happy path {nonce}"
    message_id = email.utils.make_msgid(domain=stack.mailserver.domain)
    sender, polled = stack.trusted_account, stack.polled_account

    headers = _headers(sender.address, polled.address, subject, message_id)
    inner = _inner_part(command)
    signed = stack.gpg(
        "--armor", "--detach-sign", "--digest-algo", "SHA256",
        "--local-user", stack.gpg_fingerprint, stdin=_sign_target(headers, inner),
    )
    assert signed.returncode == 0, signed.stderr.decode(errors="replace")
    armor = signed.stdout.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n").rstrip(b"\r\n")

    # The prediction, computed from the bytes on the wire — not from anything
    # the system will later say.
    raw_prompt = command.encode("utf-8")
    predicted = Prediction(
        command=command,
        sha256=hashlib.sha256(raw_prompt).hexdigest(),
        length=len(raw_prompt),
        cwd=stack.env["CLAUDE_CWD"],
        argv=["--exclude-dynamic-system-prompt-sections", "--print", command],
    )

    receipt = stack.workdir / f"cli-receipt-{nonce}.json"
    stub = Path(stack.env["CLAUDE_BIN"])
    original = _install_stub(stub, receipt)
    try:
        refused = _send(stack.mailserver, sender, polled.address,
                        _assemble(headers, inner, armor))
        assert refused == {}, f"SMTP refused recipients: {refused}"
        with stack.mailserver.imap_client(sender) as imap:
            replies = stack.mailserver.wait_for(
                imap, message_id, _tagged_replies, timeout=REPLY_TIMEOUT,
            )
        yield Observed(
            message_id=message_id, subject=subject, predicted=predicted,
            running=replies["Running"], result=replies["Result"],
            receipt_path=receipt, db_path=Path(stack.env["CHAT_DB_PATH"]),
        )
    finally:
        stub.write_bytes(original)
        stub.chmod(0o700)


def test_result_reply_carries_the_commands_effect(observed):
    """The reply mail contains exactly the effect predicted before the run.

    Reverting any link in the chain removes the observable entirely: without
    signature verification the mail is dropped unauthorised, without the
    executor no CLI output exists, without the mailer no reply is ever sent —
    and if the prompt is altered anywhere in between, the SHA-256 and the byte
    count both diverge from the prediction.
    """
    body = _body_text(observed.result)
    assert "E2E-STUB-OUTPUT" in body
    assert f"prompt-sha256: {observed.predicted.sha256}" in body
    assert f"prompt-bytes: {observed.predicted.length}" in body
    assert f"cwd: {observed.predicted.cwd}" in body


def test_result_reply_is_addressed_and_threaded(observed, stack):
    """It is a *reply*: right mailbox, right thread, tagged, and it followed an ack.

    The ack proves the reply is the product of the poller's own two-phase
    sequence rather than of a message that merely happens to be in the mailbox.
    """
    assert observed.result.get("To", "") == stack.trusted_account.address
    assert observed.result.get("In-Reply-To", "").strip() == observed.message_id
    assert observed.result.get("References", "").strip() == observed.message_id
    assert observed.result.get("Subject") == f"Re: [Result] {observed.subject}"
    assert observed.running.get("Subject") == f"Re: [Running] {observed.subject}"
    assert "Command received" in _body_text(observed.running)


def test_command_reached_the_cli_byte_for_byte(observed):
    """The filesystem receipt shows the prompt arriving unaltered in argv.

    This is the byte-exact half of the oracle: the CRLF and the two non-ASCII
    scripts written into the signed MIME part have to come out of ``execve``
    identical. It is also the check that would fail if the executor stopped
    passing ``cwd`` or dropped a flag.
    """
    receipt = json.loads(observed.receipt_path.read_text(encoding="utf-8"))
    assert receipt["prompt"] == observed.predicted.command
    assert receipt["length"] == observed.predicted.length
    assert receipt["cwd"] == observed.predicted.cwd
    assert receipt["argv"] == observed.predicted.argv


def test_the_turn_is_recorded_on_the_bus(observed):
    """The inbound turn and the outbound reply both landed in the real SQLite DB.

    Read-only, from outside the process that wrote it. Without
    ``prepare_router_command`` there is no inbound row; without
    ``record_outbound_email`` the reply's Message-ID is unknown to the bus and
    a user's follow-up reply would fail to auto-authenticate.
    """
    conn = sqlite3.connect(f"file:{observed.db_path}?mode=ro", uri=True, timeout=30)
    try:
        inbound = conn.execute(
            "SELECT from_name, to_name, body FROM messages WHERE email_message_id = ?",
            (observed.message_id,),
        ).fetchall()
        outbound = conn.execute(
            "SELECT kind FROM outbound_emails WHERE email_message_id = ?",
            (observed.result.get("Message-ID", "").strip(),),
        ).fetchall()
    finally:
        conn.close()
    assert inbound == [("user", "router", observed.predicted.command)]
    assert outbound == [("result",)]
