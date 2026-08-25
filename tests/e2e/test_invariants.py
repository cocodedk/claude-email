"""Invariants that must hold over a whole stream of real traffic.

Three properties, asserted over one generated batch of messages that were
actually put on the wire — real GreenMail in docker, real SMTP and IMAP
sockets, a real ``main.py`` process, the real SQLite bus:

1. **The shared secret never leaves the system.** No outbound mail — in any
   header value or any body part — contains it. The header half is the point:
   the leak this file was written for put the secret in the *Subject* of every
   ``[Running]`` and ``[Result]`` reply, because ``send_threaded_reply`` echoes
   the inbound Subject and the subject-bearer auth route puts ``AUTH:<secret>``
   there. A body-only check would have passed while the secret went out in the
   header of every single reply.
2. **The ledger is exact.** Every accepted inbound message produced exactly one
   execution of the CLI.
3. **Nothing runs twice.** Per accepted message: one ``[Running]``, one
   ``[Result]``, one row on the bus, one ledger line.

Why this module boots its own poller
------------------------------------
``src/security.py`` short-circuits: ``if gpg_fingerprint: return
verify_gpg_signature(...)``. The session ``stack`` fixture configures a
fingerprint, so on that poller the shared-secret routes are unreachable and the
leak cannot be observed at all. This module therefore boots a second real
``main.py`` with ``GPG_FINGERPRINT=""`` and ``SHARED_SECRET`` set — the
bearer-token deployment, supported by ``main.py``'s own startup guard, which
requires exactly one of the two. It polls the ``bystander`` mailbox so it cannot
race the session poller, and it is handed its own ``CLAUDE_BIN``, state file,
log and database.

What is *not* asserted here, and why
------------------------------------
A bearer-authenticated message re-sent under a **fresh** Message-ID executes
again, by design: no credential on that route covers any header, and
``CLAUDE.md`` records that the idempotency store is the only temporal control
there. So the duplicates in this stream are byte-identical redeliveries — same
Message-ID — and the invariant is stated per *accepted inbound message*, not
per payload. Content-bound replay of a signed message is ``test_replay.py``'s
subject and is not restated here.

Independent oracles
-------------------
The execution count comes from an append-only ledger written by the CLI
stand-in — a third-party program outside the SUT, reached by a real fork/exec
from the real ``src/executor.py``. The mail comes back off a real IMAP socket.
The bus rows are read read-only from outside the writing process. And the
secret-scanner itself is validated against the *inbound* corpus, where the
secret demonstrably is: a scanner that found nothing anywhere would otherwise
make every absence below vacuous.
"""
from __future__ import annotations

import base64
import dataclasses
import email
import email.header
import email.message
import email.utils
import json
import os
import smtplib
import sqlite3
import time
from pathlib import Path

import pytest

#: A poll cycle is 1s and the CLI timeout is 30s; allow for a cold first cycle.
REPLY_TIMEOUT = 240.0
#: After the tracer has completed, give the poller this long to do the wrong
#: thing before believing it did the right one.
SETTLE_SECONDS = 6.0

#: The stand-in for the ``claude`` CLI. Appends one JSON line per execution to
#: an append-only ledger and echoes the prompt back on stdout. The echo is
#: deliberate: the reply body is built from this output, so anything the
#: extraction path failed to strip out of the command travels back to the user
#: in the ``[Result]`` mail, where the body assertions can see it.
STUB_SOURCE = '''#!/usr/bin/env python3
"""Execution-counting stand-in for the claude CLI, installed by test_invariants."""
import json
import os
import sys

argv = sys.argv[1:]
if "--print" not in argv:
    sys.stderr.write("e2e invariants stub: no --print in argv: %r\\n" % (argv,))
    raise SystemExit(3)
prompt = argv[argv.index("--print") + 1]
with open(__LEDGER__, "a", encoding="utf-8") as handle:
    handle.write(json.dumps({"prompt": prompt, "pid": os.getpid()}) + "\\n")
sys.stdout.write("E2E-INVARIANTS-EXECUTED\\n" + prompt + "\\n")
'''


# ---------------------------------------------------------------------------
# Wire-format construction. Assembled from bytes here; nothing borrows the
# production serialiser this suite exists to test.
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


def _encoded_word(text: str) -> str:
    """RFC 2047 base64 encoded-word — what a mail client emits for non-ASCII."""
    packed = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return f"=?utf-8?B?{packed}?="


@dataclasses.dataclass(frozen=True)
class Case:
    """One generated message, and what the system is expected to do with it."""

    name: str
    nonce: str
    message_id: str
    subject: str
    body: str
    raw: bytes
    sender_login: str
    accepted: bool


def _make_cases(stack, secret: str) -> list[Case]:
    """Generate the stream: five accepted messages and one from a stranger.

    Each carries a fresh nonce, so the ledger, the mailbox and the bus can all
    be counted per message without matching on text this module also composed.
    The variation is in *where the secret sits* — every placement below is a
    different outbound surface it could escape through.
    """
    domain = stack.mailserver.domain
    trusted = stack.trusted_account.address
    polled = stack.mailserver.accounts["bystander"].address
    stranger = stack.mailserver.accounts["recipient"]
    token = f"AUTH:{secret}"
    cases: list[Case] = []

    def add(name: str, subject: str, body: str, *, accepted: bool,
            message_id: str | None = None, sender_login: str | None = None,
            sender_address: str | None = None) -> None:
        mid = message_id or email.utils.make_msgid(domain=domain)
        cases.append(Case(
            name=name, nonce=nonce, message_id=mid, subject=subject, body=body,
            raw=_raw_message(sender_address or trusted, polled, subject, mid, body),
            sender_login=sender_login or stack.trusted_account.login,
            accepted=accepted,
        ))

    # 1. The credential in the Subject — the route whose reply leaked it.
    nonce = os.urandom(8).hex()
    add("subject_secret",
        f"{token} e2e invariants {nonce}",
        f"e2e invariants {nonce}: echo this line back.",
        accepted=True)

    # 2. The credential in the body instead; the Subject is clean.
    nonce = os.urandom(8).hex()
    add("body_secret",
        f"e2e invariants {nonce}",
        f"{token}\r\ne2e invariants {nonce}: echo this line back.",
        accepted=True)

    # 3. The credential in the body *and* a bare copy of the secret in the
    #    command text. Only the ``AUTH:`` token is stripped on the way in, so
    #    the bare copy rides the prompt into the CLI and back out in the reply.
    nonce = os.urandom(8).hex()
    add("bare_secret_in_command",
        f"e2e invariants {nonce}",
        f"{token}\r\ne2e invariants {nonce}: echo this line back, "
        f"including the literal string {secret} unchanged.",
        accepted=True)

    # 4. The credential in an RFC 2047 encoded-word Subject. ``is_authorized``
    #    decodes before checking, so this authenticates — and a reply that
    #    echoes the header verbatim carries the secret out base64-wrapped,
    #    where a raw substring search would never see it.
    nonce = os.urandom(8).hex()
    add("encoded_word_subject_secret",
        _encoded_word(f"{token} e2e invariants {nonce} æøå"),
        f"e2e invariants {nonce}: echo this line back.",
        accepted=True)

    # 5. The secret inside the inbound Message-ID. Replies copy that header
    #    verbatim into In-Reply-To and References.
    nonce = os.urandom(8).hex()
    add("secret_in_message_id",
        f"e2e invariants {nonce}",
        f"{token}\r\ne2e invariants {nonce}: echo this line back.",
        message_id=f"<inv-{secret}-{nonce}@{domain}>",
        accepted=True)

    # 6. A stranger who somehow holds the secret. The envelope check runs
    #    before any credential, so this must produce nothing at all.
    nonce = os.urandom(8).hex()
    add("stranger_with_secret",
        f"{token} e2e invariants {nonce}",
        f"e2e invariants {nonce}: echo this line back.",
        accepted=False,
        sender_login=stranger.login, sender_address=stranger.address)

    return cases


def _send(mailserver, login: str, password: str, sender: str, recipient: str,
          raw: bytes) -> None:
    """Deliver over a real SMTP connection; GreenMail sets Return-Path."""
    with smtplib.SMTP(mailserver.host, mailserver.smtp_port, timeout=30) as smtp:
        smtp.login(login, password)
        refused = smtp.sendmail(sender, [recipient], raw)
    assert refused == {}, f"SMTP refused recipients: {refused}"


def _send_case(stack, case: Case) -> None:
    accounts = stack.mailserver.accounts
    account = next(a for a in accounts.values() if a.login == case.sender_login)
    _send(stack.mailserver, account.login, account.password, account.address,
          accounts["bystander"].address, case.raw)


# ---------------------------------------------------------------------------
# Reading the outside world back.
# ---------------------------------------------------------------------------

def _rfc822_bytes(fetched) -> bytes:
    """Pull the single RFC822 literal out of a FETCH response.

    A live poller flags messages from a second IMAP session, so the server
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


def _decode_header(value: str) -> str:
    """RFC 2047 encoded-words resolved to the text they stand for."""
    try:
        return str(email.header.make_header(email.header.decode_header(value)))
    except Exception:  # pragma: no cover — malformed header, use it raw
        return value


def _body_parts(message) -> list[str]:
    """Every text payload in the message, transfer-decoding undone."""
    out = []
    for part in message.walk():
        if part.is_multipart():
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        charset = part.get_content_charset() or "utf-8"
        out.append(payload.decode(charset, errors="replace"))
    return out


def find_secret(message, secret: str) -> list[str]:
    """Every surface of ``message`` on which ``secret`` is visible.

    Headers are searched both raw and RFC 2047-decoded, bodies with the
    transfer encoding undone, plus the serialised bytes as a backstop. The
    returned labels are what a failure message shows, so they name the surface
    rather than repeating the secret.
    """
    hits = []
    for name, value in message.items():
        if secret in value or secret in _decode_header(value):
            hits.append(f"header {name}")
    for index, text in enumerate(_body_parts(message)):
        if secret in text:
            hits.append(f"body part {index}")
    if secret.encode("utf-8") in message.as_bytes():
        hits.append("raw bytes")
    return hits


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
# The bearer-mode poller: a second real main.py, GPG disabled.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def bearer(stack, tmp_path_factory):
    """A real ``main.py`` booted with ``GPG_FINGERPRINT=""`` and a secret.

    ``main.py``'s startup guard requires *one* of ``GPG_FINGERPRINT`` or
    ``SHARED_SECRET``, so this is a supported deployment, not a broken one — and
    it is the only one on which the shared-secret routes are reachable, because
    ``is_authorized`` returns on the GPG branch whenever a fingerprint is set.

    Everything but the mailbox, the CLI, and the state/DB/log paths comes from
    the session stack, so the single difference this fixture introduces is the
    auth mode. It polls ``bystander`` and so cannot race the session poller.
    """
    import _stack

    workdir = tmp_path_factory.mktemp("e2e-invariants")
    (workdir / "projects").mkdir()
    (workdir / "logs").mkdir()
    ledger = workdir / "ledger.jsonl"
    cli = workdir / "claude-stub"
    cli.write_text(STUB_SOURCE.replace("__LEDGER__", repr(str(ledger))))
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
        "CHAT_DB_PATH": str(workdir / "claude-chat-invariants.db"),
    }
    assert env["SHARED_SECRET"], "the routes under test need a secret configured"
    child = _stack.spawn("poller-bearer", "main.py", env, workdir / "logs",
                         runroot=stack.runroot)
    try:
        child.wait_for_output(r"IMAP connected to \S+ as \S+")
        yield {"child": child, "ledger": ledger, "env": env,
               "secret": env["SHARED_SECRET"]}
    finally:
        child.stop()


@dataclasses.dataclass(frozen=True)
class Observed:
    """Every outside surface, snapshotted once the stack had gone quiet."""

    secret: str
    cases: list
    tracer: Case
    outbound: list
    polled_inbox: list
    executions: list
    rows: list

    def case(self, name: str) -> Case:
        return next(c for c in self.cases if c.name == name)

    def mail_for(self, case: Case, tag: str) -> list:
        """Outbound mail carrying this case's nonce and the given tag.

        Matched on the nonce rather than on ``In-Reply-To``: one case
        deliberately hides the secret *in* its Message-ID, and a reply that
        correctly refuses to echo that header cannot be threaded back to it.
        """
        found = []
        for message in self.outbound:
            subject = _decode_header(message.get("Subject", ""))
            if f"[{tag}]" not in subject:
                continue
            if case.nonce in subject or any(case.nonce in part
                                            for part in _body_parts(message)):
                found.append(message)
        return found

    def runs_of(self, case: Case) -> list:
        return [e for e in self.executions if case.nonce in e["prompt"]]

    def rows_for(self, case: Case) -> list:
        return [r for r in self.rows if case.nonce in (r["body"] or "")]


def _await_tag(stack, bearer, case: Case, tag: str) -> None:
    """Block until an outbound mail with ``tag`` carries this case's nonce."""
    def fetch(imap, _nonce):
        for message in _fetch_inbox(imap):
            subject = _decode_header(message.get("Subject", ""))
            if f"[{tag}]" in subject and (
                case.nonce in subject
                or any(case.nonce in part for part in _body_parts(message))
            ):
                return message
        raise AssertionError(f"no [{tag}] for {case.name} yet")

    with stack.mailserver.imap_client(stack.trusted_account) as imap:
        stack.mailserver.wait_for(imap, case.nonce, fetch, timeout=REPLY_TIMEOUT)


@pytest.fixture(scope="module")
def observed(stack, bearer) -> Observed:
    """Drive the whole stream through the live poller, then snapshot.

    Module-scoped: the batch costs several poll cycles and a dozen SMTP round
    trips, and every assertion below reads a different surface of the one run.
    """
    secret = bearer["secret"]
    cases = _make_cases(stack, secret)

    with stack.mailserver.imap_client(stack.trusted_account) as imap:
        before = {m.get("Message-ID", "").strip() for m in _fetch_inbox(imap)}

    for case in cases:
        _send_case(stack, case)
    for case in cases:
        if case.accepted:
            _await_tag(stack, bearer, case, "Result")

    # Byte-identical redelivery of two accepted messages: same Message-ID, same
    # bytes, same wire. The idempotency store is what must refuse these.
    for name in ("subject_secret", "body_secret"):
        _send_case(stack, next(c for c in cases if c.name == name))

    # The positive control, sent last and awaited in full: "nothing happened"
    # below is then scoped by a demonstrably awake poller rather than a timeout.
    tracer_nonce = os.urandom(8).hex()
    tracer_id = email.utils.make_msgid(domain=stack.mailserver.domain)
    tracer_subject = f"e2e invariants tracer {tracer_nonce}"
    tracer_body = (f"AUTH:{secret}\r\ne2e invariants tracer {tracer_nonce}: "
                   "echo this line back.")
    tracer = Case(
        name="tracer", nonce=tracer_nonce, message_id=tracer_id,
        subject=tracer_subject, body=tracer_body,
        raw=_raw_message(stack.trusted_account.address,
                         stack.mailserver.accounts["bystander"].address,
                         tracer_subject, tracer_id, tracer_body),
        sender_login=stack.trusted_account.login, accepted=True)
    _send_case(stack, tracer)
    _await_tag(stack, bearer, tracer, "Result")
    time.sleep(SETTLE_SECONDS)

    with stack.mailserver.imap_client(stack.trusted_account) as imap:
        outbound = [m for m in _fetch_inbox(imap)
                    if m.get("Message-ID", "").strip() not in before]
    with stack.mailserver.imap_client(
            stack.mailserver.accounts["bystander"]) as imap:
        polled_inbox = _fetch_inbox(imap)
    ledger = bearer["ledger"]
    executions = [json.loads(line) for line
                  in (ledger.read_text(encoding="utf-8").splitlines()
                      if ledger.exists() else []) if line.strip()]
    return Observed(
        secret=secret, cases=cases, tracer=tracer, outbound=outbound,
        polled_inbox=polled_inbox, executions=executions,
        rows=_bus_rows(Path(bearer["env"]["CHAT_DB_PATH"])),
    )


# ---------------------------------------------------------------------------
# Controls. Without these three, every absence asserted afterwards is vacuous.
# ---------------------------------------------------------------------------

def test_the_tracer_completed_after_the_whole_stream(observed):
    """A later message ran to completion, so the poller worked the batch."""
    assert len(observed.runs_of(observed.tracer)) == 1
    assert len(observed.mail_for(observed.tracer, "Result")) == 1


def test_the_scanner_finds_the_secret_in_the_inbound_corpus(observed):
    """The scanner used below does detect the secret where it really is.

    Every accepted message carried the credential inbound, in a Subject, a
    body or a Message-ID, and one of them base64-wrapped in an encoded-word.
    If ``find_secret`` came back empty here, the outbound assertions would be
    proving nothing but a broken search.
    """
    hits = [message for message in observed.polled_inbox
            if find_secret(message, observed.secret)]
    assert len(hits) >= len([c for c in observed.cases if c.accepted])
    encoded = observed.case("encoded_word_subject_secret")
    wrapped = [m for m in observed.polled_inbox
               if encoded.nonce in _decode_header(m.get("Subject", ""))]
    assert wrapped, "the encoded-word message never reached the mailbox"
    assert observed.secret not in wrapped[0].get("Subject", ""), (
        "the encoded-word Subject was not actually encoded, so the decoding "
        "half of the scanner is untested")
    assert find_secret(wrapped[0], observed.secret) == ["header Subject"]


def test_every_accepted_command_was_answered(observed):
    """Each accepted message got its ``[Running]`` and its ``[Result]``.

    This is what separates "the secret was redacted" from "the message was
    dropped": the replies the assertions below search really do exist.
    """
    for case in observed.cases:
        if not case.accepted:
            continue
        assert len(observed.mail_for(case, "Running")) == 1, case.name
        assert len(observed.mail_for(case, "Result")) == 1, case.name


# ---------------------------------------------------------------------------
# Invariant 1 — the secret never leaves.
# ---------------------------------------------------------------------------

def test_no_outbound_mail_header_contains_the_secret(observed):
    """Not one header value of any reply carries the secret.

    Fails if reverted: ``send_threaded_reply`` builds the reply Subject from
    the inbound Subject, and the subject-bearer auth route puts
    ``AUTH:<secret>`` there — so without redaction every ``[Running]`` and
    ``[Result]`` for the ``subject_secret`` and ``encoded_word`` cases ships
    the secret in its Subject, and the ``secret_in_message_id`` case ships it
    in In-Reply-To and References.
    """
    leaks = {
        message.get("Message-ID", ""): [
            hit for hit in find_secret(message, observed.secret)
            if hit.startswith("header")
        ]
        for message in observed.outbound
    }
    assert {k: v for k, v in leaks.items() if v} == {}


def test_no_outbound_mail_body_contains_the_secret(observed):
    """No body part of any reply carries the secret.

    Fails if reverted: the ``bare_secret_in_command`` case puts a copy of the
    secret in the command text with no ``AUTH:`` prefix, so nothing on the
    inbound path strips it; it travels into the CLI prompt and back out in the
    ``[Result]`` body.
    """
    leaks = {
        message.get("Message-ID", ""): [
            hit for hit in find_secret(message, observed.secret)
            if not hit.startswith("header")
        ]
        for message in observed.outbound
    }
    assert {k: v for k, v in leaks.items() if v} == {}


def test_no_outbound_mail_carries_the_secret_on_any_surface(observed):
    """The invariant stated whole, over every message the system emitted.

    Scoped to mail this module's poller produced — the snapshot is diffed
    against the mailbox as it stood before the batch was sent.
    """
    assert observed.outbound, "no outbound mail was captured at all"
    assert [m.get("Message-ID", "") for m in observed.outbound
            if find_secret(m, observed.secret)] == []


# ---------------------------------------------------------------------------
# Invariant 2 — the ledger is exact.
# ---------------------------------------------------------------------------

def test_every_accepted_command_has_exactly_one_ledger_row(observed):
    """One CLI execution per accepted message, and none for the stranger.

    Fails if reverted: drop the idempotency store and the two byte-identical
    redeliveries add a second execution each; drop the envelope check and the
    stranger's message adds one of its own.
    """
    counts = {c.name: len(observed.runs_of(c)) for c in observed.cases}
    assert counts == {c.name: (1 if c.accepted else 0) for c in observed.cases}


def test_the_ledger_holds_nothing_beyond_the_accepted_stream(observed):
    """The ledger's total line count is exactly the accepted messages.

    A per-case count could be satisfied while some extra execution ran under a
    prompt carrying no nonce at all; this closes that.
    """
    expected = len([c for c in observed.cases if c.accepted]) + 1  # + tracer
    assert len(observed.executions) == expected, observed.executions


# ---------------------------------------------------------------------------
# Invariant 3 — no effect observed twice.
# ---------------------------------------------------------------------------

def test_no_accepted_command_was_answered_twice(observed):
    """Exactly one ``[Running]`` and one ``[Result]`` per accepted message."""
    for case in observed.cases:
        if not case.accepted:
            continue
        assert len(observed.mail_for(case, "Running")) == 1, case.name
        assert len(observed.mail_for(case, "Result")) == 1, case.name


def test_the_bus_recorded_each_turn_exactly_once(observed):
    """One inbound row per accepted message on the real SQLite bus.

    An independent surface from the mailbox and the ledger: the row is written
    by ``prepare_router_command`` inside the poller process and read here
    read-only from outside it.
    """
    for case in observed.cases:
        inbound = [r for r in observed.rows_for(case)
                   if r["from_name"] == "user" and r["to_name"] == "router"]
        assert len(inbound) == (1 if case.accepted else 0), (case.name, inbound)


def test_the_stranger_produced_nothing(observed):
    """A sender who is not authorised gets no execution, no mail, no row.

    Holding the shared secret is not enough: the envelope check runs first.
    """
    stranger = observed.case("stranger_with_secret")
    assert observed.runs_of(stranger) == []
    assert observed.mail_for(stranger, "Running") == []
    assert observed.mail_for(stranger, "Result") == []
    assert observed.rows_for(stranger) == []
