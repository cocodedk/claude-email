"""Concurrent and duplicated real delivery must still execute exactly once.

Everything here is real: GreenMail in docker, real SMTP and IMAP sockets, the
real ``gpg`` binary, the real ``main.py`` process, the real SQLite bus. The one
stand-in is the ``claude`` CLI — a third-party program *outside* the product,
reached by a real fork/exec from the real ``src/executor.py``. Nothing in the
system under test is patched, and no assertion reads a log line.

Three concurrency shapes, one property
--------------------------------------
1. **Two different commands delivered simultaneously.** Both must run, exactly
   once each. This is the control against a barrier that is too *wide*.
2. **The same credential delivered twice in parallel, under two different
   Message-IDs.** The signature is identical, so the content-bound replay key
   from :mod:`src.replay_guard` is identical; the Message-IDs are not, so the
   Message-ID store has no opinion on either. Exactly one may run.
3. **The same message delivered twice by the mail server**, byte for byte —
   one Message-ID, two copies really sitting in the mailbox. Exactly one may
   run.

Why the deliveries have to be genuinely concurrent
--------------------------------------------------
``EmailPoller.fetch_unseen`` holds *two* barriers, and only one of them is
reachable sequentially. The persisted ``STATE_FILE`` set catches a duplicate
that arrives in a *later* poll cycle — that is what ``test_replay.py`` covers.
The in-memory ``batch`` set, consulted in the same two ``if`` statements, is
the only thing standing between a duplicate and a second execution when both
copies are fetched in the *same* cycle. A sequential test can never reach it:
by the time the second copy is sent, the first has already been marked
processed and the persisted store answers first.

So this file forces the batch. A blocking command is delivered and executed
first; the CLI stand-in sleeps inside it. While the poller is stuck in that
``execute_command``, all six messages are pushed onto the wire from six
threads released by one :class:`threading.Barrier`. The test then *proves*
they were all in the mailbox before the blocker finished — from timestamps the
stub itself wrote and an IMAP ``SEARCH`` that sets no flags — so "they landed
in one batch" is asserted, not assumed. The next ``fetch_unseen`` therefore
sees all six at once, and the batch barrier is the code under test.

Absence needs a positive control, so a differently-signed tracer is sent last
and its full round trip is awaited before any snapshot is taken.
"""
from __future__ import annotations

import dataclasses
import email
import email.message
import email.utils
import hashlib
import json
import os
import smtplib
import sqlite3
import threading
import time
from pathlib import Path

import pytest

#: A fixed MIME boundary keeps the signed bytes easy to reason about.
BOUNDARY = "e2e-concurrency-boundary"
#: A poll cycle is 1s and the CLI timeout is 30s; allow for a cold first cycle.
REPLY_TIMEOUT = 180.0
#: How long the stand-in CLI blocks inside the blocking command. Must be
#: comfortably longer than six SMTP round trips and shorter than
#: ``CLAUDE_TIMEOUT`` (30s in the harness environment).
BLOCK_SECONDS = 12.0
#: After the tracer has completed, give the poller this long to do the wrong
#: thing before believing it did the right one.
SETTLE_SECONDS = 6.0
#: Substring that makes the stand-in block. Matched as a substring rather than
#: by equality because ``prepare_router_command`` sits between the mail and the
#: CLI and may wrap the command.
BLOCK_MARKER = "E2E-CONCURRENCY-BLOCK"

#: The stand-in for the ``claude`` CLI. Appends one JSON line when it starts
#: and one when it finishes; ``O_APPEND`` on a short write is atomic, so
#: concurrent executions cannot lose or interleave a record. Counting ``start``
#: records is how this file measures "the effect happened exactly once", and
#: the wall-clock stamps are what let the test prove the six parallel messages
#: were all delivered while the blocking command was still running.
STUB_SOURCE = '''#!/usr/bin/env python3
"""Execution-counting, optionally blocking stand-in for the claude CLI."""
import hashlib
import json
import os
import sys
import time

argv = sys.argv[1:]
if "--print" not in argv:
    sys.stderr.write("e2e concurrency stub: no --print in argv: %r\\n" % (argv,))
    raise SystemExit(3)
prompt = argv[argv.index("--print") + 1]
digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def record(phase):
    with open(__LEDGER__, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"phase": phase, "prompt": prompt,
                                 "sha256": digest, "pid": os.getpid(),
                                 "at": time.time()}) + "\\n")


record("start")
if __MARKER__ in prompt:
    time.sleep(__BLOCK__)
record("end")
sys.stdout.write("E2E-CONCURRENCY-EXECUTED\\nprompt-sha256: " + digest + "\\n")
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


def _signed_command(stack, subject: str, command: str) -> Sent:
    """A real, freshly signed, authorised command mail."""
    message_id = email.utils.make_msgid(domain=stack.mailserver.domain)
    stamp = email.utils.formatdate(localtime=False)
    headers = _headers(stack.trusted_account.address,
                       stack.polled_account.address, subject, message_id, stamp)
    inner = _inner_part(command)
    signature = _armor(stack.gpg(
        "--armor", "--detach-sign", "--digest-algo", "SHA256",
        "--local-user", stack.gpg_fingerprint, stdin=_sign_target(headers, inner),
    ))
    return Sent(message_id, subject, command, _assemble(headers, inner, signature))


def _reheader(stack, captured: Sent) -> Sent:
    """The captured message's signed payload under a FRESH Message-ID.

    The captured bytes are spliced, not rebuilt: the signed MIME part and the
    detached signature are carried across untouched, and only the two header
    lines outside the signature are rewritten. Signing the same text a second
    time would not do — an OpenPGP signature packet hashes its own creation
    time at one-second resolution, so a second signature is a coin flip between
    "identical bytes" and "a different credential", and this test needs the
    credential to be provably the same one.
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


def _gpg_verifies(stack, sent: Sent, workdir: Path) -> bool:
    """An independent oracle: does the real gpg accept this message's signature?

    Run out-of-band, against the same throwaway keyring, using the same bytes
    ``src/gpg_verify.py`` would hand to gpg. If this is True and the stack
    still refused the message, the refusal is a duplicate-suppression decision
    and nothing else.
    """
    parsed = email.message_from_bytes(sent.raw)
    inner_part, sig_part = parsed.get_payload()
    stem = sent.message_id.strip("<>").replace("/", "_")
    sig_path, data_path = workdir / f"conc-{stem}.sig", workdir / f"conc-{stem}.dat"
    sig_path.write_bytes(sig_part.get_payload(decode=True))
    data_path.write_bytes(inner_part.as_bytes())
    return stack.gpg("--verify", str(sig_path), str(data_path)).returncode == 0


# ---------------------------------------------------------------------------
# Real, genuinely simultaneous delivery.
# ---------------------------------------------------------------------------

def _send(mailserver, account, recipient: str, raw: bytes) -> None:
    """Deliver over a real SMTP connection; GreenMail sets Return-Path."""
    with smtplib.SMTP(mailserver.host, mailserver.smtp_port, timeout=30) as smtp:
        smtp.login(account.login, account.password)
        refused = smtp.sendmail(account.address, [recipient], raw)
    assert refused == {}, f"SMTP refused recipients: {refused}"


@dataclasses.dataclass
class Delivery:
    """One thread's send, and when it actually happened."""

    label: str
    started_at: float = 0.0
    finished_at: float = 0.0
    error: BaseException | None = None


def _send_all_at_once(mailserver, account, recipient: str,
                      items: list[tuple[str, bytes]]) -> list[Delivery]:
    """Put every message on the wire from its own thread, released together.

    Each thread opens and authenticates its own SMTP session *before* waiting
    on the barrier, so the only thing inside the timed window is ``sendmail``
    itself. That is what makes "simultaneously" mean something here: six
    independent TCP connections to the real server, all issuing DATA within
    milliseconds of each other.
    """
    barrier = threading.Barrier(len(items))
    records = [Delivery(label) for label, _ in items]

    def worker(index: int, raw: bytes) -> None:
        record = records[index]
        try:
            with smtplib.SMTP(mailserver.host, mailserver.smtp_port, timeout=30) as smtp:
                smtp.login(account.login, account.password)
                barrier.wait(timeout=60)
                record.started_at = time.time()
                refused = smtp.sendmail(account.address, [recipient], raw)
                record.finished_at = time.time()
            assert refused == {}, f"SMTP refused recipients: {refused}"
        except BaseException as exc:  # noqa: BLE001 — re-raised by the caller
            record.error = exc
            barrier.abort()

    threads = [threading.Thread(target=worker, args=(i, raw), daemon=True)
               for i, (_, raw) in enumerate(items)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    failures = [(r.label, r.error) for r in records if r.error is not None]
    assert not failures, f"parallel delivery failed: {failures}"
    return records


# ---------------------------------------------------------------------------
# Reading the outside world back.
# ---------------------------------------------------------------------------

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
    """Every message currently in ``INBOX``, parsed.

    Only ever called *after* the run is complete: ``FETCH (RFC822)`` sets
    ``\\Seen``, and the poller searches ``UNSEEN``, so running this mid-flight
    would quietly eat the messages under test.
    """
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


def _count_in_mailbox(imap, message_id: str) -> int:
    """How many copies of ``message_id`` the server is holding, flag-free.

    ``SEARCH`` reads no message body and sets no flags, which is the whole
    point: this runs while the poller is mid-command and must leave every one
    of those messages ``UNSEEN`` for it to find.
    """
    status, _ = imap.select("INBOX")
    assert status == "OK", f"IMAP SELECT INBOX failed: {status}"
    status, data = imap.search(None, "HEADER", "Message-ID", f'"{message_id}"')
    assert status == "OK", f"IMAP SEARCH HEADER failed: {status}"
    return len(data[0].split())


def _bus_rows(db_path: Path) -> list:
    """Read-only snapshot of the real SQLite bus, from outside the writer."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(
            "SELECT from_name, to_name, body, email_message_id FROM messages")]
    finally:
        conn.close()


def _stub_source(ledger: Path) -> str:
    """The stand-in CLI, bound to one ledger."""
    return (STUB_SOURCE
            .replace("__LEDGER__", repr(str(ledger)))
            .replace("__MARKER__", repr(BLOCK_MARKER))
            .replace("__BLOCK__", repr(BLOCK_SECONDS)))


def _install_stub(path: Path, ledger: Path) -> bytes:
    """Swap in the counting CLI, returning the bytes it displaced."""
    original = path.read_bytes()
    path.write_text(_stub_source(ledger))
    path.chmod(0o700)
    return original


def _read_ledger(ledger: Path) -> list:
    if not ledger.exists():
        return []
    return [json.loads(line) for line in
            ledger.read_text(encoding="utf-8").splitlines() if line.strip()]


def _await_block_started(ledger: Path, timeout: float = REPLY_TIMEOUT) -> float:
    """Block until the stand-in has entered the blocking command.

    Returns the moment it started. From here until ``BLOCK_SECONDS`` later the
    poller is inside ``execute_command`` and cannot issue another SEARCH — the
    window this whole file is built around.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for record in _read_ledger(ledger):
            if record["phase"] == "start" and BLOCK_MARKER in record["prompt"]:
                return record["at"]
        time.sleep(0.1)
    raise AssertionError(f"blocking command never started within {timeout}s")


def _await_tag(stack, sent: Sent, tag: str, timeout: float = REPLY_TIMEOUT) -> None:
    """Block until a reply carrying ``tag`` is threaded on ``sent``."""
    def fetch(imap, _nonce):
        found = [m for m in _fetch_inbox(imap)
                 if m.get("In-Reply-To", "").strip() == sent.message_id
                 and f"[{tag}]" in m.get("Subject", "")]
        assert found, f"no [{tag}] for {sent.subject} yet"
        return found

    with stack.mailserver.imap_client(stack.trusted_account) as imap:
        stack.mailserver.wait_for(imap, sent.subject, fetch, timeout=timeout)


@dataclasses.dataclass(frozen=True)
class Observed:
    """Every outside surface, snapshotted once the stack had gone quiet."""

    blocker: Sent
    alpha: Sent
    beta: Sent
    nonce_first: Sent
    nonce_second: Sent
    duplicate: Sent
    tracer: Sent
    deliveries: list
    block_started_at: float
    block_ended_at: float
    mailbox_confirmed_at: float
    copies_in_mailbox: dict
    nonce_verifies: dict
    sender_inbox: list
    polled_inbox: list
    ledger: list
    rows: list

    @property
    def executions(self) -> list:
        return [r for r in self.ledger if r["phase"] == "start"]

    def replies_to(self, sent: Sent) -> list:
        return [m for m in self.sender_inbox
                if m.get("In-Reply-To", "").strip() == sent.message_id]

    def tagged(self, sent: Sent, tag: str) -> list:
        return [m for m in self.replies_to(sent) if f"[{tag}]" in m.get("Subject", "")]

    def runs_of(self, sent: Sent) -> list:
        """Executions of exactly this command.

        Matched by equality, not containment: no message here carries an
        ``In-Reply-To``, so ``build_email_thread_transcript`` finds no anchor
        and the prompt handed to the CLI is the bare command body. Equality
        also means one command quoting another could never inflate the other's
        count.
        """
        return [e for e in self.executions if e["prompt"] == sent.command]

    def rows_for(self, sent: Sent) -> list:
        return [r for r in self.rows if r["email_message_id"] == sent.message_id]


@pytest.fixture(scope="module")
def observed(stack) -> Observed:
    """Deliver six messages at once into one poll batch, then snapshot.

    Module-scoped: the sequence costs a blocking execution plus several poll
    cycles and eight SMTP round trips, and every assertion below reads a
    different surface of the same run.
    """
    nonce = os.urandom(12).hex()
    ledger = stack.workdir / f"concurrency-ledger-{nonce}.jsonl"
    stub = Path(stack.env["CLAUDE_BIN"])
    original = _install_stub(stub, ledger)
    try:
        yield _run(stack, nonce, ledger)
    finally:
        stub.write_bytes(original)
        stub.chmod(0o700)


def _build_messages(stack, nonce: str) -> dict:
    """Every message this run puts on the wire, signed for real."""
    alpha = _signed_command(
        stack, f"e2e concurrency alpha {nonce}",
        f"e2e concurrency alpha {nonce}: report the digest of this prompt.")
    beta = _signed_command(
        stack, f"e2e concurrency beta {nonce}",
        f"e2e concurrency beta {nonce}: report the digest of this prompt.")
    nonce_first = _signed_command(
        stack, f"e2e concurrency nonce {nonce}",
        f"e2e concurrency nonce {nonce}: report the digest of this prompt.")
    duplicate = _signed_command(
        stack, f"e2e concurrency duplicate {nonce}",
        f"e2e concurrency duplicate {nonce}: report the digest of this prompt.")
    return {
        "alpha": alpha, "beta": beta,
        "nonce_first": nonce_first,
        # Same signature packet, same signed body, fresh Message-ID: the
        # credential is provably identical and the Message-ID store has never
        # seen either one.
        "nonce_second": _reheader(stack, nonce_first),
        "duplicate": duplicate,
    }


def _run(stack, nonce: str, ledger: Path) -> Observed:
    blocker = _signed_command(
        stack, f"e2e concurrency blocker {nonce}",
        f"e2e concurrency blocker {nonce} {BLOCK_MARKER}: hold the poller.")
    _send(stack.mailserver, stack.trusted_account,
          stack.polled_account.address, blocker.raw)
    block_started_at = _await_block_started(ledger)

    messages = _build_messages(stack, nonce)
    # The duplicate goes on the wire twice, byte for byte — a real double
    # delivery by the real mail server, not a re-serialisation of it.
    items = [(name, messages[name].raw) for name in
             ("alpha", "beta", "nonce_first", "nonce_second", "duplicate")]
    items.append(("duplicate_again", messages["duplicate"].raw))
    deliveries = _send_all_at_once(
        stack.mailserver, stack.trusted_account,
        stack.polled_account.address, items)

    # Flag-free confirmation that the server really is holding all of them,
    # taken while the poller is still blocked. This is the evidence that the
    # next fetch_unseen saw one batch containing every one of them.
    with stack.mailserver.imap_client(stack.polled_account) as imap:
        copies = {name: _count_in_mailbox(imap, sent.message_id)
                  for name, sent in messages.items()}
    mailbox_confirmed_at = time.time()

    tracer = _signed_command(
        stack, f"e2e concurrency tracer {nonce}",
        f"e2e concurrency tracer {nonce}: report the digest of this prompt.")
    for sent in (messages["alpha"], messages["beta"]):
        _await_tag(stack, sent, "Result")
    _send(stack.mailserver, stack.trusted_account,
          stack.polled_account.address, tracer.raw)
    _await_tag(stack, tracer, "Result")
    time.sleep(SETTLE_SECONDS)

    with stack.mailserver.imap_client(stack.trusted_account) as imap:
        sender_inbox = _fetch_inbox(imap)
    with stack.mailserver.imap_client(stack.polled_account) as imap:
        polled_inbox = _fetch_inbox(imap)
    records = _read_ledger(ledger)
    ends = [r["at"] for r in records
            if r["phase"] == "end" and BLOCK_MARKER in r["prompt"]]
    assert len(ends) == 1, f"blocking command did not finish exactly once: {ends}"
    return Observed(
        blocker=blocker, tracer=tracer, deliveries=deliveries,
        block_started_at=block_started_at, block_ended_at=ends[0],
        mailbox_confirmed_at=mailbox_confirmed_at, copies_in_mailbox=copies,
        nonce_verifies={
            name: _gpg_verifies(stack, messages[name], stack.workdir)
            for name in ("nonce_first", "nonce_second")},
        sender_inbox=sender_inbox, polled_inbox=polled_inbox,
        ledger=records, rows=_bus_rows(Path(stack.env["CHAT_DB_PATH"])),
        **messages,
    )


# ---------------------------------------------------------------------------
# The setup this file's conclusions depend on, asserted before them.
# ---------------------------------------------------------------------------

def test_the_six_messages_were_delivered_simultaneously(observed):
    """Six independent SMTP sessions issued DATA inside one shared window.

    Not a proxy: each thread authenticated *before* the barrier, so the
    recorded window contains only ``sendmail``. If the barrier were removed and
    the sends serialised, the last send would start after the first finished
    and this fails.
    """
    assert len(observed.deliveries) == 6
    latest_start = max(d.started_at for d in observed.deliveries)
    earliest_finish = min(d.finished_at for d in observed.deliveries)
    assert latest_start < earliest_finish, [
        (d.label, d.started_at, d.finished_at) for d in observed.deliveries]


def test_every_message_reached_the_mailbox_before_the_blocker_finished(observed):
    """All six were sitting UNSEEN in the polled mailbox in one poll window.

    This is what makes the rest of the file a test of the *in-batch* barrier
    rather than of the persisted ``STATE_FILE``. The poller was inside
    ``execute_command`` for the blocking message from ``block_started_at``
    until ``block_ended_at`` and could issue no SEARCH in between; the
    flag-free IMAP confirmation that every message had arrived completed
    inside that window, so the next ``fetch_unseen`` saw them all at once.
    """
    assert observed.copies_in_mailbox == {
        "alpha": 1, "beta": 1, "nonce_first": 1, "nonce_second": 1,
        "duplicate": 2,
    }, observed.copies_in_mailbox
    assert observed.block_started_at < observed.mailbox_confirmed_at, (
        "the mailbox was confirmed before the blocking command even started")
    assert observed.mailbox_confirmed_at < observed.block_ended_at, (
        f"delivery finished {observed.mailbox_confirmed_at - observed.block_ended_at:.1f}s "
        "after the blocker released the poller — the messages may have been "
        "split across poll batches, so this run proves nothing about the "
        "in-batch barrier")


def test_the_tracer_completed_after_the_whole_batch(observed):
    """A later, differently-signed command ran to completion.

    Sent after the parallel batch and awaited in full, so the poller
    demonstrably worked through it. Every "nothing happened" assertion below
    is scoped by that fact rather than by a timeout.
    """
    assert len(observed.runs_of(observed.tracer)) == 1
    assert len(observed.tagged(observed.tracer, "Result")) == 1


def test_both_copies_of_the_replayed_credential_are_valid_signatures(observed):
    """A separate, real ``gpg --verify`` accepts both parallel copies.

    Without this the suppression below could be a signature failure wearing a
    duplicate-suppression costume. It also pins that the two copies carry the
    *same* credential under different Message-IDs — the only configuration in
    which the content-bound replay key, not the Message-ID store, is what
    refuses the second one.
    """
    assert observed.nonce_verifies == {"nonce_first": True, "nonce_second": True}
    assert observed.nonce_first.message_id != observed.nonce_second.message_id
    assert observed.nonce_first.command == observed.nonce_second.command


# ---------------------------------------------------------------------------
# The property: exactly one execution, under all three concurrency shapes.
# ---------------------------------------------------------------------------

def test_two_simultaneous_distinct_commands_both_ran_exactly_once(observed):
    """Concurrency must not cost a command its execution.

    The control against a barrier that is too wide: ``alpha`` and ``beta``
    arrive in the same batch from the same sender with the same structure, and
    differ only in their body and their signature. Fails if the in-batch
    barrier keyed on anything coarser — the sender, the Subject, the poll
    cycle — because then only one of the two would run.
    """
    for sent in (observed.alpha, observed.beta):
        runs = observed.runs_of(sent)
        assert len(runs) == 1, (sent.subject, [r["sha256"] for r in runs])
        assert len(observed.tagged(sent, "Running")) == 1
        assert len(observed.tagged(sent, "Result")) == 1
    assert observed.alpha.command != observed.beta.command


def test_the_same_credential_delivered_twice_in_parallel_ran_once(observed):
    """One execution across both parallel copies of the same signature.

    This is the acceptance criterion's hardest case and the only one a
    sequential test cannot reach. Both copies are unknown to the persisted
    ``STATE_FILE`` — different Message-IDs, neither ever processed — so the
    only thing that can refuse the second is the in-batch ``batch`` set in
    ``EmailPoller.fetch_unseen`` comparing content-bound replay keys.

    Fails if reverted: drop ``key in batch`` from that check and both copies
    are authorised, executed and answered, leaving two ledger rows here.
    """
    runs = observed.runs_of(observed.nonce_first)
    assert len(runs) == 1, [r["sha256"] for r in runs]
    replied = [sent for sent in (observed.nonce_first, observed.nonce_second)
               if observed.tagged(sent, "Result")]
    assert len(replied) == 1, [s.message_id for s in replied]
    refused = (observed.nonce_second if replied[0] is observed.nonce_first
               else observed.nonce_first)
    assert observed.replies_to(refused) == [], (
        [m.get("Subject") for m in observed.replies_to(refused)])
    assert observed.rows_for(refused) == []
    assert len(observed.tagged(replied[0], "Running")) == 1


def test_the_same_message_delivered_twice_by_the_server_ran_once(observed):
    """Two byte-identical copies really in the mailbox, one execution.

    Real IMAP re-delivery: the same bytes were accepted twice by GreenMail and
    both copies are still there (asserted above), sharing one Message-ID and
    one signature.

    Which barrier holds it, stated precisely: the copies share a Message-ID
    *and* a signature, and for that shape the two in-batch guards are fully
    redundant. ``fetch_unseen`` checks the Message-ID first
    (``src/poller.py:157``), so that is the guard that actually fires; the
    replay key at ``src/poller.py:167`` would have refused the copy too. So
    this test fails only when **both** in-batch guards are dropped, and it is
    not the single-mutation pin for either of them. Those pins are
    ``test_the_same_credential_delivered_twice_in_parallel_ran_once`` for
    ``key in batch`` — a fresh Message-ID leaves nothing else to catch it —
    and ``test_an_unsigned_command_redelivered_in_parallel_ran_once`` for
    ``msg_id in batch``, on the bearer route at the bottom of this file, where
    there is no content-bound credential to fall back on. Stated here rather
    than claiming a precedence the code does not have.
    """
    runs = observed.runs_of(observed.duplicate)
    assert len(runs) == 1, [r["sha256"] for r in runs]
    assert runs[0]["sha256"] == observed.duplicate.prompt_sha256
    assert len(observed.tagged(observed.duplicate, "Running")) == 1
    assert len(observed.tagged(observed.duplicate, "Result")) == 1


def test_the_batch_produced_no_execution_beyond_the_expected_ones(observed):
    """Nothing ran twice and nothing ran that was not sent.

    Counted over the whole ledger rather than per command, so a duplicate
    execution cannot hide behind a per-command filter. Six commands were
    accepted from eight deliveries; the two suppressed copies are the two the
    criterion is about.
    """
    accepted = (observed.blocker, observed.alpha, observed.beta,
                observed.nonce_first, observed.duplicate, observed.tracer)
    assert len(observed.executions) == len(accepted), [
        r["prompt"][:60] for r in observed.executions]
    for sent in accepted:
        assert len(observed.runs_of(sent)) == 1, sent.subject


def test_the_bus_recorded_each_accepted_turn_exactly_once(observed):
    """One inbound row per accepted command, and none for the suppressed ones.

    An independent surface from the mailbox and the ledger: the rows are
    written by ``prepare_router_command`` inside the poller process and read
    here read-only from outside it.
    """
    for sent in (observed.alpha, observed.beta, observed.duplicate,
                 observed.tracer):
        inbound = [r for r in observed.rows
                   if r["from_name"] == "user" and r["to_name"] == "router"
                   and r["body"] == sent.command]
        assert len(inbound) == 1, (sent.subject, inbound)
        assert inbound[0]["email_message_id"] == sent.message_id
    nonce_rows = [r for r in observed.rows
                  if r["from_name"] == "user" and r["to_name"] == "router"
                  and r["body"] == observed.nonce_first.command]
    assert len(nonce_rows) == 1, nonce_rows


# ---------------------------------------------------------------------------
# The bearer-token deployment, where the Message-ID is the only barrier.
#
# Above, both duplicate shapes carry a GPG signature, so at least one guard
# always has an answer: the byte-identical pair is refused on its shared
# Message-ID (checked first) and would be refused on its shared replay key
# too, and the fresh-Message-ID pair is refused on the replay key alone. On
# the shared-secret routes there *is* no content-bound credential —
# ``replay_key`` returns ``""`` and ``fetch_unseen`` treats that as "no
# opinion" — so a byte-identical re-delivery inside one poll batch is held by
# the Message-ID barrier alone. That is the branch this section exercises, and
# it is only reachable on a poller booted with ``GPG_FINGERPRINT=""``:
# ``is_authorized`` returns on the GPG branch whenever a fingerprint is set.
# ---------------------------------------------------------------------------

def _raw_message(sender: str, recipient: str, subject: str, message_id: str,
                 body: str) -> bytes:
    """A plain ``text/plain`` mail, serialised by hand."""
    head = "\r\n".join((
        f"From: {sender}", f"To: {recipient}", f"Subject: {subject}",
        f"Message-ID: {message_id}",
        f"Date: {email.utils.formatdate(localtime=False)}",
        "MIME-Version: 1.0",
        "Content-Type: text/plain; charset=utf-8",
        "Content-Transfer-Encoding: 8bit",
    ))
    return head.encode("utf-8") + b"\r\n\r\n" + body.encode("utf-8") + b"\r\n"


def _bearer_command(stack, mailbox, subject: str, command: str) -> Sent:
    """An authorised command that presents the shared secret and nothing else."""
    message_id = email.utils.make_msgid(domain=stack.mailserver.domain)
    body = f"AUTH:{stack.shared_secret}\r\n{command}"
    raw = _raw_message(stack.trusted_account.address, mailbox.address,
                       subject, message_id, body)
    return Sent(message_id, subject, command, raw)


@pytest.fixture(scope="module")
def bearer(stack, tmp_path_factory):
    """A second real ``main.py``, booted with ``GPG_FINGERPRINT=""``.

    ``main.py``'s startup guard requires *one* of ``GPG_FINGERPRINT`` or
    ``SHARED_SECRET``, so this is a supported deployment rather than a broken
    one. Everything but the mailbox, the CLI and the state/DB/log paths comes
    from the session stack, so the single difference is the auth mode. It polls
    ``bystander`` and therefore cannot race the session poller.
    """
    import _stack

    workdir = tmp_path_factory.mktemp("e2e-concurrency-bearer")
    (workdir / "projects").mkdir()
    (workdir / "logs").mkdir()
    ledger = workdir / "ledger.jsonl"
    cli = workdir / "claude-stub"
    cli.write_text(_stub_source(ledger))
    cli.chmod(0o700)

    mailbox = stack.mailserver.accounts["bystander"]
    env = {
        **stack.env,
        "EMAIL_ADDRESS": mailbox.login, "EMAIL_PASSWORD": mailbox.password,
        "GPG_FINGERPRINT": "",
        "CLAUDE_BIN": str(cli),
        "CLAUDE_CWD": str(workdir / "projects"),
        "STATE_FILE": str(workdir / "processed_ids.json"),
        "LOG_FILE": str(workdir / "claude-email.log"),
        "CHAT_DB_PATH": str(workdir / "claude-chat-concurrency.db"),
    }
    assert env["SHARED_SECRET"], "the route under test needs a secret configured"
    child = _stack.spawn("poller-bearer", "main.py", env, workdir / "logs",
                         runroot=stack.runroot)
    try:
        child.wait_for_output(r"IMAP connected to \S+ as \S+")
        yield {"child": child, "ledger": ledger, "env": env, "mailbox": mailbox}
    finally:
        child.stop()


@dataclasses.dataclass(frozen=True)
class BearerObserved:
    """The bearer run's outside surfaces."""

    duplicate: Sent
    other: Sent
    deliveries: list
    block_started_at: float
    block_ended_at: float
    mailbox_confirmed_at: float
    copies_in_mailbox: int
    replay_key: str
    sender_inbox: list
    ledger: list

    @property
    def executions(self) -> list:
        return [r for r in self.ledger if r["phase"] == "start"]

    def replies_to(self, sent: Sent) -> list:
        return [m for m in self.sender_inbox
                if m.get("In-Reply-To", "").strip() == sent.message_id]

    def tagged(self, sent: Sent, tag: str) -> list:
        return [m for m in self.replies_to(sent) if f"[{tag}]" in m.get("Subject", "")]

    def runs_of(self, sent: Sent) -> list:
        return [e for e in self.executions if e["prompt"] == sent.command]


@pytest.fixture(scope="module")
def bearer_observed(stack, bearer) -> BearerObserved:
    """Re-deliver one unsigned command twice, in parallel, into one batch."""
    from src.replay_guard import replay_key

    nonce = os.urandom(12).hex()
    ledger, mailbox = bearer["ledger"], bearer["mailbox"]
    blocker = _bearer_command(
        stack, mailbox, f"e2e concurrency bearer blocker {nonce}",
        f"e2e concurrency bearer blocker {nonce} {BLOCK_MARKER}: hold the poller.")
    _send(stack.mailserver, stack.trusted_account, mailbox.address, blocker.raw)
    block_started_at = _await_block_started(ledger)

    duplicate = _bearer_command(
        stack, mailbox, f"e2e concurrency bearer duplicate {nonce}",
        f"e2e concurrency bearer duplicate {nonce}: report this line back.")
    other = _bearer_command(
        stack, mailbox, f"e2e concurrency bearer other {nonce}",
        f"e2e concurrency bearer other {nonce}: report this line back.")
    deliveries = _send_all_at_once(
        stack.mailserver, stack.trusted_account, mailbox.address,
        [("duplicate", duplicate.raw), ("duplicate_again", duplicate.raw),
         ("other", other.raw)])

    with stack.mailserver.imap_client(mailbox) as imap:
        copies = _count_in_mailbox(imap, duplicate.message_id)
    mailbox_confirmed_at = time.time()

    _await_tag(stack, other, "Result")
    time.sleep(SETTLE_SECONDS)

    with stack.mailserver.imap_client(stack.trusted_account) as imap:
        sender_inbox = _fetch_inbox(imap)
    records = _read_ledger(ledger)
    ends = [r["at"] for r in records
            if r["phase"] == "end" and BLOCK_MARKER in r["prompt"]]
    assert len(ends) == 1, f"blocking command did not finish exactly once: {ends}"
    return BearerObserved(
        duplicate=duplicate, other=other, deliveries=deliveries,
        block_started_at=block_started_at, block_ended_at=ends[0],
        mailbox_confirmed_at=mailbox_confirmed_at, copies_in_mailbox=copies,
        replay_key=replay_key(email.message_from_bytes(duplicate.raw)),
        sender_inbox=sender_inbox, ledger=records,
    )


def test_the_bearer_duplicate_carries_no_content_bound_credential(bearer_observed):
    """``replay_key`` has no opinion about this message.

    Asked of the production function itself, against the exact bytes that went
    on the wire. An empty key is what makes the Message-ID the *only* barrier
    for this delivery — if this ever returns a key, the test below stops
    exercising the branch it claims to.
    """
    assert bearer_observed.replay_key == ""


def test_the_bearer_duplicate_reached_the_mailbox_twice_in_one_window(bearer_observed):
    """Two byte-identical copies, both in the mailbox while the poller blocked."""
    assert bearer_observed.copies_in_mailbox == 2
    assert (bearer_observed.block_started_at
            < bearer_observed.mailbox_confirmed_at
            < bearer_observed.block_ended_at), (
        "the duplicates may have been split across poll batches, so this run "
        "proves nothing about the in-batch barrier")
    latest_start = max(d.started_at for d in bearer_observed.deliveries)
    earliest_finish = min(d.finished_at for d in bearer_observed.deliveries)
    assert latest_start < earliest_finish


def test_an_unsigned_command_redelivered_in_parallel_ran_once(bearer_observed):
    """One execution and one reply pair, from two identical deliveries.

    Fails if reverted: this is the delivery the Message-ID half of
    ``fetch_unseen``'s duplicate check exists for. Drop ``msg_id in batch`` and
    both copies are fetched in the same cycle before either is recorded in
    ``STATE_FILE``, there is no replay key to fall back on, and the command
    runs twice. The signed cases above would not notice that regression —
    for signed mail the two guards are redundant, so the replay key still
    refuses the duplicate — which is exactly why this section is here.
    """
    runs = bearer_observed.runs_of(bearer_observed.duplicate)
    assert len(runs) == 1, [r["sha256"] for r in runs]
    assert len(bearer_observed.tagged(bearer_observed.duplicate, "Running")) == 1
    assert len(bearer_observed.tagged(bearer_observed.duplicate, "Result")) == 1


def test_the_bearer_batch_ran_nothing_else(bearer_observed):
    """The distinct command in the same batch still ran, exactly once.

    The control against over-suppression on this route: ``other`` shares the
    sender, the mailbox, the credential and the poll batch with the duplicate
    pair, and differs only in its Message-ID and its body.
    """
    assert len(bearer_observed.runs_of(bearer_observed.other)) == 1
    assert len(bearer_observed.tagged(bearer_observed.other, "Result")) == 1
    assert len(bearer_observed.executions) == 3, [
        r["prompt"][:60] for r in bearer_observed.executions]
