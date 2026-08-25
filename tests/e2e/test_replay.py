"""Replaying a captured real message must never produce a second effect.

The message this file replays is not synthesised for the occasion: it is the
exact byte string a real, authorised, GPG-signed command was delivered as,
captured off the wire and handed back to the same live stack — real GreenMail
in docker, real SMTP and IMAP sockets, the real ``gpg`` binary, the real
``main.py`` process, the real SQLite bus. Nothing in the system under test is
patched. The only stand-in is the ``claude`` CLI, a third-party program
*outside* the product, reached by a real fork/exec from the real
``src/executor.py``.

Two replays, and why the second one is the point
------------------------------------------------
1. **Byte-identical re-delivery.** Same Message-ID, same headers, same
   signature. The poller's ``processed_ids`` store already refuses this.
2. **The same payload under a FRESH Message-ID** (and a bumped ``Date``). This
   is the one that matters. *No credential this system accepts covers the
   Message-ID header* — the GPG signature is over the MIME part alone, so an
   interceptor can rewrite the header freely and the signature still verifies.
   Message-ID dedupe is therefore an idempotency store, not replay protection,
   and before this slice the second replay executed the command a second time.

The test proves the distinction rather than assuming it: it verifies the
replayed message's signature with a *separate, real* ``gpg --verify`` call, so
the refusal cannot be confused with "the mail was corrupted in transit", and it
asserts the message is sitting in the polled mailbox, so it cannot be confused
with "the mail was never delivered".

What is asserted is the *effect*, not a log line
------------------------------------------------
The CLI stand-in appends one line to an append-only ledger every time it is
executed. The assertions count executions in that ledger, ``[Result]`` mails in
the sender's inbox, and rows on the bus. A rejection that still ran the command
fails here; a log message saying "replay refused" proves nothing and is never
read.

Absence is only meaningful with a positive control, so a differently-signed
tracer command is sent *after* both replays and its own round trip is awaited
in full before any snapshot is taken. "Not yet processed" therefore cannot
masquerade as "refused".
"""
from __future__ import annotations

import base64
import dataclasses
import email
import email.message
import email.utils
import hashlib
import json
import os
import smtplib
import sqlite3
import time
from pathlib import Path

import pytest

#: A fixed MIME boundary keeps the signed bytes easy to reason about.
BOUNDARY = "e2e-replay-boundary"
#: A poll cycle is 1s and the CLI timeout is 30s; allow for a cold first cycle.
REPLY_TIMEOUT = 180.0
#: After the tracer has completed, give the poller this long to do the wrong
#: thing before believing it did the right one.
SETTLE_SECONDS = 6.0

#: The stand-in for the ``claude`` CLI. Appends one JSON line per execution to
#: a shared ledger; ``O_APPEND`` on a short write is atomic, so concurrent
#: executions cannot lose or interleave a record. Counting lines is how this
#: file measures "the effect happened exactly once".
STUB_SOURCE = '''#!/usr/bin/env python3
"""Execution-counting stand-in for the claude CLI, installed by test_replay."""
import hashlib
import json
import os
import sys

argv = sys.argv[1:]
if "--print" not in argv:
    sys.stderr.write("e2e replay stub: no --print in argv: %r\\n" % (argv,))
    raise SystemExit(3)
prompt = argv[argv.index("--print") + 1]
raw = prompt.encode("utf-8")
digest = hashlib.sha256(raw).hexdigest()
with open(__LEDGER__, "a", encoding="utf-8") as handle:
    handle.write(json.dumps({"prompt": prompt, "sha256": digest,
                             "pid": os.getpid(), "cwd": os.getcwd()}) + "\\n")
sys.stdout.write("E2E-REPLAY-EXECUTED\\nprompt-sha256: " + digest + "\\n")
'''


# ---------------------------------------------------------------------------
# Wire-format construction. Assembled from bytes here; nothing borrows the
# production serialiser this suite exists to test.
# ---------------------------------------------------------------------------

def _headers(sender: str, recipient: str, subject: str, message_id: str,
             date: str) -> bytes:
    lines = (
        f"From: {sender}", f"To: {recipient}", f"Subject: {subject}",
        f"Message-ID: {message_id}", f"Date: {date}", "MIME-Version: 1.0",
        'Content-Type: multipart/signed; protocol="application/pgp-signature";'
        f' micalg=pgp-sha256; boundary="{BOUNDARY}"',
    )
    return "\r\n".join(lines).encode("utf-8") + b"\r\n"


def _inner_part(body: str) -> bytes:
    """The signed MIME part. The trailing CRLF belongs to the boundary."""
    return (
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"Content-Transfer-Encoding: 8bit\r\n"
        b"\r\n" + body.encode("utf-8") + b"\r\n"
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


def _sign_target(headers: bytes, inner: bytes) -> bytes:
    """The bytes a verifier will hand to gpg.

    ``src/gpg_verify.py`` verifies ``part.as_bytes()`` of the *parsed* message
    and the stdlib parser normalises line endings on reserialisation. Rather
    than guess at that transformation, run it: parse a copy of the very message
    about to be sent and sign whatever comes out.
    """
    placeholder = _assemble(headers, inner, b"placeholder")
    return email.message_from_bytes(placeholder).get_payload()[0].as_bytes()


def _armor(completed) -> bytes:
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    out = completed.stdout.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    return out.rstrip(b"\r\n")


@dataclasses.dataclass(frozen=True)
class Sent:
    """One message that was actually put on the wire."""

    message_id: str
    subject: str
    command: str
    raw: bytes

    @property
    def prompt_sha256(self) -> str:
        return hashlib.sha256(self.command.encode("utf-8")).hexdigest()


def _sign(stack, headers: bytes, inner: bytes) -> bytes:
    return _armor(stack.gpg(
        "--armor", "--detach-sign", "--digest-algo", "SHA256",
        "--local-user", stack.gpg_fingerprint, stdin=_sign_target(headers, inner),
    ))


def _signed_command(stack, subject: str, command: str, *,
                    date: str | None = None) -> Sent:
    """A real, freshly signed, authorised command mail."""
    message_id = email.utils.make_msgid(domain=stack.mailserver.domain)
    stamp = date or email.utils.formatdate(localtime=False)
    headers = _headers(stack.trusted_account.address,
                       stack.polled_account.address, subject, message_id, stamp)
    inner = _inner_part(command)
    return Sent(message_id, subject, command,
                _assemble(headers, inner, _sign(stack, headers, inner)))


def _reheader(stack, captured: Sent) -> Sent:
    """The captured message's signed payload under a FRESH Message-ID.

    The captured bytes are spliced, not rebuilt: the body — the signed MIME
    part and the detached signature — is carried across untouched, and only two
    header lines outside the signature are rewritten (a new Message-ID and a
    ``Date`` an hour later). That is exactly the edit an interceptor can make,
    and exactly the edit no credential in this system covers. Re-serialising
    through the ``email`` package instead would risk the refusal being about a
    line ending rather than about replay.
    """
    head, sep, body = captured.raw.partition(b"\r\n\r\n")
    assert sep, "captured message had no header/body separator"

    message_id = email.utils.make_msgid(domain=stack.mailserver.domain)
    later = email.utils.formatdate(time.time() + 3600, localtime=False)
    rewritten, seen = [], set()
    for line in head.split(b"\r\n"):
        lowered = line.lower()
        if lowered.startswith(b"message-id:"):
            seen.add("mid")
            line = f"Message-ID: {message_id}".encode("utf-8")
        elif lowered.startswith(b"date:"):
            seen.add("date")
            line = f"Date: {later}".encode("utf-8")
        rewritten.append(line)
    assert seen == {"mid", "date"}, f"headers not both rewritten: {sorted(seen)}"

    raw = b"\r\n".join(rewritten) + sep + body
    assert raw.partition(b"\r\n\r\n")[2] == body, "body was altered by the splice"
    assert captured.message_id.encode() not in raw, "old Message-ID survived"
    return Sent(message_id, captured.subject, captured.command, raw)


def _debinarise(armour: bytes) -> bytes:
    """The binary signature packet inside an armoured block.

    Computed here with nothing but ``base64``, so the test is not asking the
    production dearmourer whether the production dearmourer is right.
    """
    _, blank, rest = armour.replace(b"\r\n", b"\n").strip().partition(b"\n\n")
    assert blank, "captured signature was not armoured"
    data = b"".join(line.strip() for line in rest.split(b"\n")
                    if line.strip() and not line.startswith((b"=", b"-----END")))
    return base64.b64decode(data, validate=True)


def _binary_signature_replay(stack, captured: Sent) -> Sent:
    """The captured signature, re-sent as a *binary* detached signature.

    Same signed MIME part, same signature packet, fresh Message-ID — only the
    armour is gone, and ``src/gpg_verify.py`` hands the decoded part straight to
    gpg either way. This is the transformation that catches a replay key
    computed over the *packaging* rather than over the credential: re-armouring
    or de-armouring a captured signature must not mint a new key.
    """
    parsed = email.message_from_bytes(captured.raw)
    inner_part, sig_part = parsed.get_payload()
    packet = _debinarise(sig_part.get_payload(decode=True))

    message_id = email.utils.make_msgid(domain=stack.mailserver.domain)
    later = email.utils.formatdate(time.time() + 7200, localtime=False)
    headers = _headers(stack.trusted_account.address,
                       stack.polled_account.address,
                       captured.subject, message_id, later)
    encoded = base64.encodebytes(packet).replace(b"\n", b"\r\n").rstrip(b"\r\n")
    marker = BOUNDARY.encode()
    raw = (
        headers
        + b"\r\n--" + marker + b"\r\n" + _inner_part(captured.command)
        + b"--" + marker + b"\r\n"
        b'Content-Type: application/pgp-signature; name="signature.asc"\r\n'
        b"Content-Transfer-Encoding: base64\r\n"
        b"\r\n" + encoded + b"\r\n"
        b"--" + marker + b"--\r\n"
    )
    assert b"-----BEGIN PGP SIGNATURE-----" not in raw, "armour survived"
    return Sent(message_id, captured.subject, captured.command, raw)


def _gpg_verifies(stack, sent: Sent, workdir: Path) -> bool:
    """An independent oracle: does the real gpg accept this message's signature?

    Run out-of-band, against the same throwaway keyring, using the same bytes
    ``src/gpg_verify.py`` would hand to gpg. If this is True and the stack still
    refused the message, the refusal is a replay decision and nothing else.
    """
    parsed = email.message_from_bytes(sent.raw)
    inner_part, sig_part = parsed.get_payload()
    sig_path = workdir / f"verify-{sent.message_id.strip('<>')}.sig"
    data_path = workdir / f"verify-{sent.message_id.strip('<>')}.dat"
    sig_path.write_bytes(sig_part.get_payload(decode=True))
    data_path.write_bytes(inner_part.as_bytes())
    return stack.gpg("--verify", str(sig_path), str(data_path)).returncode == 0


def _send(mailserver, account, recipient: str, raw: bytes) -> None:
    """Deliver over a real SMTP connection; GreenMail sets Return-Path."""
    with smtplib.SMTP(mailserver.host, mailserver.smtp_port, timeout=30) as smtp:
        smtp.login(account.login, account.password)
        refused = smtp.sendmail(account.address, [recipient], raw)
    assert refused == {}, f"SMTP refused recipients: {refused}"


def _rfc822_bytes(fetched) -> bytes:
    """Pull the single RFC822 literal out of a FETCH response.

    The live poller flags messages from a second IMAP session, so the server
    interleaves untagged ``* n FETCH (FLAGS (\\Seen))`` lines into a concurrent
    fetch; imaplib returns those as bare ``bytes`` in the same list.
    """
    literals = [part[1] for part in fetched
                if isinstance(part, tuple) and isinstance(part[1], (bytes, bytearray))]
    assert len(literals) == 1, f"expected one RFC822 literal, got {fetched!r}"
    return literals[0]


def _fetch_inbox(imap) -> list:
    """Every message currently in ``INBOX``, parsed."""
    status, _ = imap.select("INBOX")
    assert status == "OK", f"IMAP SELECT INBOX failed: {status}"
    status, data = imap.search(None, "ALL")
    assert status == "OK", f"IMAP SEARCH failed: {status}"
    out = []
    for uid in data[0].split():
        status, fetched = imap.fetch(uid, "(RFC822)")
        assert status == "OK", f"IMAP FETCH failed: {status}"
        out.append(email.message_from_bytes(_rfc822_bytes(fetched)))
    return out


def _install_stub(path: Path, ledger: Path) -> bytes:
    """Swap in the counting CLI, returning the bytes it displaced."""
    original = path.read_bytes()
    path.write_text(STUB_SOURCE.replace("__LEDGER__", repr(str(ledger))))
    path.chmod(0o700)
    return original


@dataclasses.dataclass(frozen=True)
class Observed:
    """Every outside surface, snapshotted once the stack had gone quiet."""

    captured: Sent
    reheadered: Sent
    binary: Sent
    tracer: Sent
    reheadered_verifies: bool
    binary_verifies: bool
    sender_inbox: list
    polled_inbox: list
    executions: list
    rows: list

    def replies_to(self, sent: Sent) -> list:
        return [m for m in self.sender_inbox
                if m.get("In-Reply-To", "").strip() == sent.message_id]

    def tagged(self, sent: Sent, tag: str) -> list:
        return [m for m in self.replies_to(sent) if f"[{tag}]" in m.get("Subject", "")]

    def runs_of(self, sent: Sent) -> list:
        return [e for e in self.executions if e["prompt"] == sent.command]


def _await_tracer(stack, tracer: Sent) -> None:
    """Block until the tracer's own ``[Result]`` has been delivered."""
    def fetch(imap, _nonce):
        results = [m for m in _fetch_inbox(imap)
                   if m.get("In-Reply-To", "").strip() == tracer.message_id
                   and "[Result]" in m.get("Subject", "")]
        assert results, "tracer has not completed yet"
        return results

    with stack.mailserver.imap_client(stack.trusted_account) as imap:
        stack.mailserver.wait_for(imap, "tracer", fetch, timeout=REPLY_TIMEOUT)


def _await_result(stack, sent: Sent) -> None:
    def fetch(imap, _nonce):
        found = {tag for m in _fetch_inbox(imap)
                 if m.get("In-Reply-To", "").strip() == sent.message_id
                 for tag in ("Running", "Result") if f"[{tag}]" in m.get("Subject", "")}
        assert found == {"Running", "Result"}, f"only got {sorted(found)}"
        return found

    with stack.mailserver.imap_client(stack.trusted_account) as imap:
        stack.mailserver.wait_for(imap, "original", fetch, timeout=REPLY_TIMEOUT)


@pytest.fixture(scope="module")
def observed(stack) -> Observed:
    """Run one command, replay it twice, and snapshot the outside world.

    Module-scoped: the whole sequence costs several poll cycles plus four SMTP
    round trips, and every assertion below reads a different surface of the
    same run.
    """
    nonce = os.urandom(12).hex()
    command = (f"e2e replay {nonce}: report the digest of this prompt.\r\n"
               "second line, non-ASCII: æøå سلام")
    ledger = stack.workdir / f"replay-ledger-{nonce}.jsonl"
    stub = Path(stack.env["CLAUDE_BIN"])
    original = _install_stub(stub, ledger)
    try:
        captured = _signed_command(stack, f"e2e replay {nonce}", command)
        _send(stack.mailserver, stack.trusted_account,
              stack.polled_account.address, captured.raw)
        _await_result(stack, captured)

        # Replay 1 — the captured bytes, unchanged, down the same wire.
        _send(stack.mailserver, stack.trusted_account,
              stack.polled_account.address, captured.raw)
        # Replay 2 — the same signed payload, fresh Message-ID, bumped Date.
        reheadered = _reheader(stack, captured)
        _send(stack.mailserver, stack.trusted_account,
              stack.polled_account.address, reheadered.raw)
        # Replay 3 — the same signature packet stripped of its armour.
        binary = _binary_signature_replay(stack, captured)
        _send(stack.mailserver, stack.trusted_account,
              stack.polled_account.address, binary.raw)

        # The positive control, sent last and awaited in full.
        tracer = _signed_command(
            stack, f"e2e replay tracer {nonce}",
            f"e2e replay tracer {nonce}: report the digest of this prompt.")
        _send(stack.mailserver, stack.trusted_account,
              stack.polled_account.address, tracer.raw)
        _await_tracer(stack, tracer)
        time.sleep(SETTLE_SECONDS)

        with stack.mailserver.imap_client(stack.trusted_account) as imap:
            sender_inbox = _fetch_inbox(imap)
        with stack.mailserver.imap_client(stack.polled_account) as imap:
            polled_inbox = _fetch_inbox(imap)
        executions = [json.loads(line) for line in
                      ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
        yield Observed(
            captured=captured, reheadered=reheadered, binary=binary, tracer=tracer,
            reheadered_verifies=_gpg_verifies(stack, reheadered, stack.workdir),
            binary_verifies=_gpg_verifies(stack, binary, stack.workdir),
            sender_inbox=sender_inbox, polled_inbox=polled_inbox,
            executions=executions, rows=_bus_rows(Path(stack.env["CHAT_DB_PATH"])),
        )
    finally:
        stub.write_bytes(original)
        stub.chmod(0o700)


def _bus_rows(db_path: Path) -> list:
    """Read-only snapshot of the real SQLite bus, from outside the writer."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(
            "SELECT from_name, to_name, body, email_message_id FROM messages")]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The positive control comes first: without it every absence below is worthless.
# ---------------------------------------------------------------------------

def test_the_tracer_completed_after_both_replays(observed):
    """A later, differently-signed command ran to completion.

    Sent after both replays, so the poller demonstrably worked through the
    batch containing them. Every "nothing happened" assertion below is scoped
    by that fact rather than by a timeout.
    """
    assert len(observed.runs_of(observed.tracer)) == 1
    assert len(observed.tagged(observed.tracer, "Result")) == 1


# ---------------------------------------------------------------------------
# Both replays were genuinely delivered and genuinely authentic. Without these
# two, "refused" is indistinguishable from "never arrived" or "corrupted".
# ---------------------------------------------------------------------------

def test_both_replays_reached_the_polled_mailbox(observed):
    """The mail server accepted and stored both replayed messages.

    The byte-identical replay shares its Message-ID with the original, so the
    mailbox must hold that ID twice; the re-headered one must be there under
    its own new ID. Whatever refused them, it was not the transport.
    """
    ids = [m.get("Message-ID", "").strip() for m in observed.polled_inbox]
    assert ids.count(observed.captured.message_id) == 2, ids
    assert ids.count(observed.reheadered.message_id) == 1, ids
    assert ids.count(observed.binary.message_id) == 1, ids


def test_the_reheadered_replay_is_a_valid_signature(observed):
    """A separate, real ``gpg --verify`` accepts the re-headered message.

    This is the finding the slice exists to pin: rewriting the Message-ID and
    the ``Date`` leaves the signature intact, because the signature covers the
    MIME part and nothing else. So the message is cryptographically
    indistinguishable from the original, and only a content-bound replay check
    can tell them apart.
    """
    assert observed.reheadered_verifies, (
        "re-headered replay failed gpg verification — the test would then be "
        "proving signature validation, not replay protection")
    assert observed.reheadered.message_id != observed.captured.message_id
    assert observed.reheadered.command == observed.captured.command


def test_the_binary_signature_replay_is_also_a_valid_signature(observed):
    """gpg accepts the same signature packet with its armour stripped.

    So the armour is packaging, not credential — which is why the replay key is
    computed over the dearmoured packet. A key taken over the armour text would
    be defeated by exactly this re-encoding, with gpg none the wiser.
    """
    assert observed.binary_verifies, (
        "binary detached signature failed gpg verification — the refusal below "
        "would then prove nothing about replay protection")
    assert b"-----BEGIN PGP SIGNATURE-----" not in observed.binary.raw


# ---------------------------------------------------------------------------
# The effect, counted.
# ---------------------------------------------------------------------------

def test_the_command_executed_exactly_once(observed):
    """One execution of the CLI for the command, across all three deliveries.

    This is the acceptance criterion in its strongest form: it counts the
    *effect*, not a rejection log line. Fails if reverted — with replay
    protection keyed on the Message-ID alone, the re-headered delivery is a
    brand-new message to the poller and the ledger gains a second line.
    """
    runs = observed.runs_of(observed.captured)
    assert len(runs) == 1, [r["sha256"] for r in runs]
    assert runs[0]["sha256"] == observed.captured.prompt_sha256


def test_byte_identical_redelivery_produced_no_second_reply(observed):
    """Exactly one ``[Running]`` and one ``[Result]`` on that Message-ID.

    Fails if reverted: drop the ``processed_ids`` store, or stop consulting it
    in ``fetch_unseen``, and the second delivery yields a second pair.
    """
    assert len(observed.tagged(observed.captured, "Running")) == 1
    assert len(observed.tagged(observed.captured, "Result")) == 1


def test_the_fresh_message_id_replays_produced_nothing_at_all(observed):
    """No mail and no bus row is threaded on either fresh Message-ID.

    Fails if reverted: without a content-bound key the poller has never seen
    these Message-IDs, so it authorises, executes and replies — a ``[Running]``
    and a ``[Result]`` threaded right here. The binary variant fails the same
    way if the key is computed over the armour text instead of the packet.
    """
    for replay in (observed.reheadered, observed.binary):
        assert observed.replies_to(replay) == [], (
            [m.get("Subject") for m in observed.replies_to(replay)])
        assert [r for r in observed.rows
                if r["email_message_id"] == replay.message_id] == []


def test_the_bus_recorded_the_turn_exactly_once(observed):
    """One inbound row for the command, under the original's Message-ID only.

    An independent surface from the mailbox and the ledger: the row is written
    by ``prepare_router_command`` inside the poller process and read here
    read-only from outside it.
    """
    inbound = [r for r in observed.rows
               if r["from_name"] == "user" and r["to_name"] == "router"
               and r["body"] == observed.captured.command]
    assert len(inbound) == 1, inbound
    assert inbound[0]["email_message_id"] == observed.captured.message_id
