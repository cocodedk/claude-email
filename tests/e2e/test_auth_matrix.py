"""The full authentication matrix, driven over real mail.

Rows are the six inbound routes claude-email exposes — plaintext command,
JSON envelope, thread reply, ``@agent``, meta (status / spawn / restart) and
reaction. Columns are the five conditions a message can arrive in — unsigned,
wrong key, stale timestamp, replayed nonce, valid. All thirty cells are
asserted against a live stack: a real GreenMail server, real SMTP and IMAP
sockets, the real ``gpg`` binary over throwaway keyrings, the real ``main.py``
process and the real SQLite bus. Nothing in the system under test is patched;
the only stand-in is the ``claude`` CLI, which is a third-party program
*outside* the SUT and is reached by a real fork/exec from the real executor.

The cell this file exists for
-----------------------------
``src/json_handler.py`` gated its shared-secret comparison on the secret being
configured (``if expected and env.auth != expected``). A deployment that
authenticates with GPG alone — which ``main.py``'s startup guard explicitly
permits — therefore accepted **every** JSON envelope from anyone able to forge
a ``From``/``Return-Path`` pair, with no credential at all. Unit tests missed
it because one of them pinned the hole as intended behaviour
(``tests/json_handler/test_command.py::test_no_auth_required_when_universe_secret_empty``).
Seeing it takes a second real poller booted with ``SHARED_SECRET=""``, which
:func:`secretless` provides.

What each condition means per route
-----------------------------------
The credential differs by route, so "unsigned" and "wrong key" are expressed
in the currency each route actually accepts:

* plaintext / ``@agent`` / meta — the credential is a GPG signature over the
  MIME part, checked against ``GPG_FINGERPRINT``. Unsigned is a bare
  ``text/plain`` mail; wrong key is a real detached signature made by a real
  second key in a *separate* GNUPGHOME, so the cell is red whether the
  verifier checks fingerprint equality or merely "some key in the keyring".
* JSON envelope — the credential is ``meta.auth``. GPG never enters this path.
* thread reply / reaction — the credential is *possession of a Message-ID the
  system itself issued*. Forging one is the negative, so those cells carry a
  fabricated ``In-Reply-To``.

Stale timestamp is a finding, not a control
--------------------------------------------
claude-email has no freshness window: neither the ``Date`` header nor the
envelope's ``meta.sent_at`` is compared against the clock on any route. The
stale cells therefore assert **acceptance**, which is the truthful state of the
system, and the only temporal control is the replayed-nonce column — the
poller's ``Message-ID`` idempotency store. See ``docs/e2e-auth-matrix.md``.

Why these tests fail if the implementation is reverted
------------------------------------------------------
Per test, in the docstrings. In summary: the accepting cells assert a
route-specific downstream effect (a CLI receipt digest, an ``ack`` envelope, a
bus row, a ``[Status]`` listing) that only exists if auth, routing and
execution all ran; the rejecting cells assert the total absence of any reply
*and* of any bus row, verified only after a signed tracer sent afterwards has
completed its own round trip, so "not yet processed" cannot masquerade as
"rejected"; and the replay cells assert an exact count of one.
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
import time
from pathlib import Path

import pytest

#: A poll cycle is 1s; allow generously for SMTP hand-off plus a cold cycle.
REPLY_TIMEOUT = 180.0
#: Poll cycles to let pass after a tracer's reply lands, before concluding
#: that a message sent *before* the tracer produced no effect. IMAP delivery
#: order is not guaranteed, so the tracer alone is not quite a barrier.
SETTLE_SECONDS = 6.0
#: A fixed, long-past instant for the stale-timestamp column (2001-09-09).
STALE_EPOCH = 1_000_000_000
BOUNDARY = "e2e-auth-matrix-boundary"

#: Deterministic stand-in for the ``claude`` CLI. Reports a pure function of
#: the prompt it was handed and drops a receipt keyed by that digest, so a
#: single installation serves every plaintext cell without clobbering.
STUB_SOURCE = '''#!/usr/bin/env python3
"""Deterministic stand-in for the claude CLI, installed by test_auth_matrix."""
import hashlib
import json
import os
import sys

argv = sys.argv[1:]
if "--print" not in argv:
    sys.stderr.write("e2e stub: no --print in argv: %r\\n" % (argv,))
    raise SystemExit(3)
prompt = argv[argv.index("--print") + 1]
digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
with open(os.path.join(__RECEIPTS__, digest + ".json"), "w", encoding="utf-8") as fh:
    json.dump({"argv": argv, "prompt": prompt, "cwd": os.getcwd()}, fh)
sys.stdout.write("E2E-AUTH-MATRIX\\nprompt-sha256: " + digest + "\\n")
'''


# ---------------------------------------------------------------------------
# Wire-format construction. Every message this file sends is assembled here
# from bytes; nothing borrows the production serialiser it is meant to test.
# ---------------------------------------------------------------------------

def _date(stale: bool) -> str:
    return email.utils.formatdate(STALE_EPOCH if stale else None, localtime=False)


def _headers(sender: str, recipient: str, subject: str, message_id: str,
             content_type: str, *, stale: bool, in_reply_to: str = "",
             extra: tuple = ()) -> bytes:
    lines = [
        f"From: {sender}", f"To: {recipient}", f"Subject: {subject}",
        f"Message-ID: {message_id}", f"Date: {_date(stale)}",
        "MIME-Version: 1.0", f"Content-Type: {content_type}", *extra,
    ]
    if in_reply_to:
        lines += [f"In-Reply-To: {in_reply_to}", f"References: {in_reply_to}"]
    return "\r\n".join(lines).encode("utf-8") + b"\r\n"


def _inner_part(body: str) -> bytes:
    """The signed MIME part. The trailing CRLF belongs to the boundary."""
    return (
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"Content-Transfer-Encoding: 8bit\r\n"
        b"\r\n" + body.encode("utf-8") + b"\r\n"
    )


def _assemble_signed(headers: bytes, inner: bytes, signature: bytes) -> bytes:
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
    """The bytes a verifier hands to gpg.

    ``src/gpg_verify.py`` verifies ``part.as_bytes()`` of the *parsed*
    message, and the stdlib parser normalises line endings on
    reserialisation. Rather than guess at that transformation, run it: parse a
    copy of the very message about to be sent and sign whatever comes out.
    """
    placeholder = _assemble_signed(headers, inner, b"placeholder")
    return email.message_from_bytes(placeholder).get_payload()[0].as_bytes()


def _armor(completed) -> bytes:
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    out = completed.stdout.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    return out.rstrip(b"\r\n")


def plain_mail(sender: str, recipient: str, subject: str, message_id: str,
               body: str, *, stale: bool = False, in_reply_to: str = "") -> bytes:
    """A bare ``text/plain`` mail — no signature, no secret, no credential."""
    return (
        _headers(sender, recipient, subject, message_id,
                 "text/plain; charset=utf-8", stale=stale,
                 in_reply_to=in_reply_to,
                 extra=("Content-Transfer-Encoding: 8bit",))
        + b"\r\n" + body.encode("utf-8") + b"\r\n"
    )


def signed_mail(sign, sender: str, recipient: str, subject: str,
                message_id: str, body: str, *, stale: bool = False,
                in_reply_to: str = "") -> bytes:
    """A PGP/MIME mail detached-signed by ``sign`` (a real gpg invocation)."""
    headers = _headers(
        sender, recipient, subject, message_id,
        'multipart/signed; protocol="application/pgp-signature";'
        f' micalg=pgp-sha256; boundary="{BOUNDARY}"',
        stale=stale, in_reply_to=in_reply_to,
    )
    inner = _inner_part(body)
    return _assemble_signed(headers, inner, _armor(sign(_sign_target(headers, inner))))


def json_mail(sender: str, recipient: str, subject: str, message_id: str,
              payload: dict, *, stale: bool = False) -> bytes:
    """A single-part ``application/json`` mail — the app's own wire shape."""
    body = json.dumps(payload).encode("utf-8")
    return (
        _headers(sender, recipient, subject, message_id,
                 "application/json; charset=utf-8", stale=stale,
                 extra=("Content-Transfer-Encoding: 8bit",))
        + b"\r\n" + body + b"\r\n"
    )


def send(mailserver, account, recipient: str, raw: bytes) -> None:
    """Deliver over a real SMTP connection; GreenMail sets Return-Path."""
    with smtplib.SMTP(mailserver.host, mailserver.smtp_port, timeout=30) as smtp:
        smtp.login(account.login, account.password)
        refused = smtp.sendmail(account.address, [recipient], raw)
    assert refused == {}, f"SMTP refused recipients: {refused}"


def rfc822_bytes(fetched) -> bytes:
    """Pull the single RFC822 literal out of a FETCH response.

    The live poller flags messages from a second IMAP session, which makes the
    server interleave untagged ``* n FETCH (FLAGS (\\Seen))`` lines into any
    concurrent fetch. imaplib returns those in the same list as bare ``bytes``
    rather than ``(descriptor, literal)`` tuples, so indexing ``[0][1]``
    blindly can hand back one byte of a flag update instead of the message.
    """
    literals = [part[1] for part in fetched
                if isinstance(part, tuple) and isinstance(part[1], (bytes, bytearray))]
    assert len(literals) == 1, f"expected one RFC822 literal, got {fetched!r}"
    return literals[0]


def fetch_inbox(imap) -> list:
    """Every message currently in ``INBOX``, parsed."""
    status, _ = imap.select("INBOX")
    assert status == "OK", f"IMAP SELECT INBOX failed: {status}"
    status, data = imap.search(None, "ALL")
    assert status == "OK", f"IMAP SEARCH failed: {status}"
    out = []
    for uid in data[0].split():
        status, fetched = imap.fetch(uid, "(RFC822)")
        assert status == "OK", f"IMAP FETCH failed: {status}"
        out.append(email.message_from_bytes(rfc822_bytes(fetched)))
    return out


def body_text(message: email.message.Message) -> str:
    """Decoded text of a mail, concatenating the leaves of a multipart."""
    if message.is_multipart():
        return "\n".join(
            (part.get_payload(decode=True) or b"").decode(
                part.get_content_charset() or "utf-8", errors="replace")
            for part in message.walk() if not part.is_multipart())
    payload = message.get_payload(decode=True)
    assert payload is not None, "message had no decodable payload"
    return payload.decode(message.get_content_charset() or "utf-8", errors="replace")


def replies_in(inbox: list, message_id: str) -> list:
    return [m for m in inbox if m.get("In-Reply-To", "").strip() == message_id]


# ---------------------------------------------------------------------------
# The matrix itself.
# ---------------------------------------------------------------------------

ROUTES = ("plaintext", "json", "thread_reply", "agent", "meta", "reaction")
#: Conditions that must be refused outright. ``stale`` and ``valid`` are
#: accepted; ``replay`` is asserted as "no *second* effect" and so is carried
#: on the valid cell rather than as a cell of its own.
REJECTED = ("unsigned", "wrong_key")
ACCEPTED = ("stale", "valid")
CONDITIONS = REJECTED + ACCEPTED

ALPHA = "agent-e2e-alpha"
BETA = "agent-e2e-beta"

#: How many reply mails an accepted cell must produce. Plaintext is the only
#: two-phase route: an immediate ``[Running]`` ack and then the ``[Result]``.
EXPECTED_REPLIES = {
    "plaintext": 2, "json": 1, "thread_reply": 1,
    "agent": 1, "meta": 1, "reaction": 1,
}


@dataclasses.dataclass(frozen=True)
class Cell:
    """One (route, condition) message: what was sent and what must follow."""

    route: str
    condition: str
    nonce: str
    subject: str
    message_id: str
    raw: bytes
    command: str = ""

    @property
    def accepted(self) -> bool:
        return self.condition in ACCEPTED

    @property
    def prompt_sha256(self) -> str:
        return hashlib.sha256(self.command.encode("utf-8")).hexdigest()


@dataclasses.dataclass(frozen=True)
class Anchor:
    """A genuine outbound Message-ID plus the bus row it was issued for."""

    db_id: int
    email_message_id: str


@dataclasses.dataclass(frozen=True)
class Observed:
    """Snapshot of every outside surface, taken once the stack went quiet."""

    cells: dict
    anchors: dict
    inbox: list
    rows: list
    agents: list
    receipts: Path
    agents_before: frozenset

    def cell(self, route: str, condition: str) -> Cell:
        return self.cells[(route, condition)]

    def replies(self, cell: Cell) -> list:
        return replies_in(self.inbox, cell.message_id)

    def bus_rows_matching(self, text: str) -> list:
        return [r for r in self.rows if text in (r["body"] or "")]

    def bus_replies_to(self, anchor: Anchor) -> list:
        return [r for r in self.rows
                if r["in_reply_to"] == anchor.db_id and r["from_name"] == "user"]


def _seed_bus(db_path: str, workdir: Path) -> tuple:
    """Register two agents and post four ``ask`` messages for them.

    Written through the production ``ChatDB`` against the very database the
    running poller reads — real store, real schema, no mock. ``ask`` is the
    one message type ``src/chat_relay._should_relay`` always relays, so each
    row becomes a real outbound email whose Message-ID is the credential the
    thread-reply and reaction rows need. The PID recorded is this pytest
    process, which is genuinely alive, so the liveness reaper and the wake
    watcher both leave the rows alone for the duration of the run.
    """
    from src.chat_db import ChatDB

    chat_db = ChatDB(db_path)
    before = frozenset(a["name"] for a in chat_db.list_agents())
    home = str(workdir / "agent-home")
    for name in (ALPHA, BETA):
        chat_db.register_agent(name, home, os.getpid())
    asks = {}
    for key, agent in (("thread_reply_valid", ALPHA), ("thread_reply_stale", ALPHA),
                       ("reaction_valid", BETA), ("reaction_stale", BETA)):
        nonce = os.urandom(8).hex()
        row = chat_db.insert_message(
            agent, "user", f"e2e anchor {key} {nonce}: standing by?", "ask")
        asks[key] = (row["id"], nonce)
    return before, asks


def _collect_anchors(mailserver, account, asks: dict) -> dict:
    """Wait for the real relay to mail each seeded ask out, and note its ID."""
    def fetch(imap, _nonce):
        inbox = fetch_inbox(imap)
        found = {}
        for key, (db_id, nonce) in asks.items():
            for message in inbox:
                if nonce in body_text(message):
                    found[key] = Anchor(db_id, message.get("Message-ID", "").strip())
                    break
        assert set(found) == set(asks), f"only relayed {sorted(found)}"
        for key, anchor in found.items():
            assert anchor.email_message_id, f"{key} relayed without a Message-ID"
        return found

    with mailserver.imap_client(account) as imap:
        return mailserver.wait_for(imap, "anchors", fetch, timeout=REPLY_TIMEOUT)


def _body_mail(condition: str, ctx: dict, subject: str, message_id: str,
               body: str, *, in_reply_to: str = "") -> bytes:
    """Apply the GPG-credential column to a body-carrying route."""
    args = (ctx["sender"].address, ctx["polled"].address, subject, message_id, body)
    if condition == "unsigned":
        return plain_mail(*args, in_reply_to=in_reply_to)
    if condition == "wrong_key":
        return signed_mail(ctx["sign_foreign"], *args, in_reply_to=in_reply_to)
    return signed_mail(ctx["sign_ok"], *args, stale=condition == "stale",
                       in_reply_to=in_reply_to)


def _thread_mail(condition: str, ctx: dict, subject: str, message_id: str,
                 body: str, in_reply_to: str) -> bytes:
    """Apply the possession-of-a-Message-ID column to a thread-shaped route.

    Note the asymmetry with :func:`_body_mail`: the accepted cells here are
    *unsigned*, because on this route the credential is the ``In-Reply-To``
    itself. The negatives keep the same shape and forge that value.
    """
    args = (ctx["sender"].address, ctx["polled"].address, subject, message_id, body)
    if condition == "wrong_key":
        return signed_mail(ctx["sign_foreign"], *args, in_reply_to=in_reply_to)
    return plain_mail(*args, stale=condition == "stale", in_reply_to=in_reply_to)


#: Meta subjects, chosen so all three meta verbs appear in the row. ``spawn``
#: and ``restart`` sit in the rejected cells on purpose: those are the two that
#: start processes, and proving they do *not* run without a credential is
#: worth more than watching a successful spawn.
META_SUBJECTS = {
    "unsigned": "spawn e2e-forged-project",
    "wrong_key": "restart chat",
    "stale": "status",
    "valid": "status",
}


def _build_cell(route: str, condition: str, ctx: dict) -> Cell:
    nonce = os.urandom(8).hex()
    message_id = email.utils.make_msgid(domain=ctx["domain"])
    forged = email.utils.make_msgid(domain=ctx["domain"])
    accepted = condition in ACCEPTED

    if route == "plaintext":
        subject = f"e2e plaintext {condition} {nonce}"
        command = f"e2e auth matrix {nonce}: report the digest of this prompt."
        return Cell(route, condition, nonce, subject, message_id,
                    _body_mail(condition, ctx, subject, message_id, command), command)

    if route == "json":
        subject = f"e2e envelope {condition} {nonce}"
        meta = {"client": "e2e/1.0",
                "sent_at": "2001-09-09T01:46:40+00:00" if condition == "stale"
                else email.utils.format_datetime(email.utils.parsedate_to_datetime(
                    email.utils.formatdate(localtime=False)))}
        if condition == "wrong_key":
            meta["auth"] = f"not-the-secret-{nonce}"
        elif accepted:
            meta["auth"] = ctx["secret"]
        payload = {"v": 1, "kind": "list_projects", "body": nonce, "meta": meta}
        return Cell(route, condition, nonce, subject, message_id,
                    json_mail(ctx["sender"].address, ctx["polled"].address, subject,
                              message_id, payload, stale=condition == "stale"))

    if route == "thread_reply":
        subject = f"Re: [{ALPHA}] message"
        body = f"acknowledged {nonce}"
        anchor = ctx["anchors"][f"thread_reply_{condition}"] if accepted else None
        return Cell(route, condition, nonce, subject, message_id,
                    _thread_mail(condition, ctx, subject, message_id, body,
                                 anchor.email_message_id if anchor else forged))

    if route == "agent":
        subject = f"@{ALPHA} errand {nonce}"
        body = f"{nonce} run the alpha errand"
        return Cell(route, condition, nonce, subject, message_id,
                    _body_mail(condition, ctx, subject, message_id, body))

    if route == "meta":
        subject = META_SUBJECTS[condition]
        body = f"e2e meta {condition} {nonce}"
        return Cell(route, condition, nonce, subject, message_id,
                    _body_mail(condition, ctx, subject, message_id, body))

    assert route == "reaction", route
    subject = f"Re: [{BETA}] message"
    body = f"[thumbsup] E2E Sender reacted to your message {nonce}"
    anchor = ctx["anchors"][f"reaction_{condition}"] if accepted else None
    return Cell(route, condition, nonce, subject, message_id,
                _thread_mail(condition, ctx, subject, message_id, body,
                             anchor.email_message_id if anchor else forged))


def _await_tracer(mailserver, account, tracer: Cell) -> None:
    def fetch(imap, _):
        found = [m for m in replies_in(fetch_inbox(imap), tracer.message_id)
                 if "[Result]" in m.get("Subject", "")]
        assert found, "tracer has not completed its round trip yet"
        return found

    with mailserver.imap_client(account) as imap:
        mailserver.wait_for(imap, tracer.message_id, fetch, timeout=REPLY_TIMEOUT)
    # The tracer proves the poller reached *its* message. IMAP delivery order
    # is not guaranteed, so give anything queued alongside it a few more poll
    # cycles before concluding it produced nothing.
    time.sleep(SETTLE_SECONDS)


def _snapshot_bus(db_path: str) -> tuple:
    """Read the real SQLite bus, read-only, from outside the writing process."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT from_name, to_name, body, type, in_reply_to FROM messages")]
        agents = [r[0] for r in conn.execute("SELECT name FROM agents")]
    finally:
        conn.close()
    return rows, agents


@pytest.fixture(scope="module")
def observed(stack, tmp_path_factory) -> Observed:
    """Send all thirty cells through the live stack, then snapshot the world.

    Module-scoped: every assertion below reads a different surface of one
    run, and a run costs several poll cycles plus SMTP delivery.

    The second GPG key lives in its own GNUPGHOME, never imported into the
    stack's keyring. That makes the wrong-key column red whether
    ``verify_gpg_signature`` compares fingerprints or merely asks "was this
    signed by a key I hold" — the poller cannot even resolve the signer.
    """
    import _stack

    domain = stack.mailserver.domain
    foreign_home = tmp_path_factory.mktemp("e2e-foreign") / "gnupg"
    receipts = stack.workdir / "auth-matrix-receipts"
    receipts.mkdir(exist_ok=True)
    stub = Path(stack.env["CLAUDE_BIN"])
    displaced = stub.read_bytes()
    stub.write_text(STUB_SOURCE.replace("__RECEIPTS__", repr(str(receipts))))
    stub.chmod(0o700)
    try:
        foreign_fpr = _stack.generate_gpg_key(
            foreign_home, f"e2e foreign key <e2e-foreign@{domain}>")
        try:
            agents_before, asks = _seed_bus(stack.env["CHAT_DB_PATH"], stack.workdir)
            anchors = _collect_anchors(stack.mailserver, stack.trusted_account, asks)
            ctx = {
                "domain": domain,
                "sender": stack.trusted_account,
                "polled": stack.polled_account,
                "secret": stack.shared_secret,
                "anchors": anchors,
                "sign_ok": lambda data: stack.gpg(
                    "--armor", "--detach-sign", "--digest-algo", "SHA256",
                    "--local-user", stack.gpg_fingerprint, stdin=data),
                "sign_foreign": lambda data: _stack.gpg(
                    foreign_home, "--armor", "--detach-sign", "--digest-algo",
                    "SHA256", "--local-user", foreign_fpr, stdin=data),
            }
            cells = {(route, condition): _build_cell(route, condition, ctx)
                     for route in ROUTES for condition in CONDITIONS}

            deliver = lambda raw: send(  # noqa: E731 — one-line partial
                stack.mailserver, stack.trusted_account,
                stack.polled_account.address, raw)
            for cell in cells.values():
                deliver(cell.raw)
            tracer = _build_cell("plaintext", "valid", ctx)
            deliver(tracer.raw)
            _await_tracer(stack.mailserver, stack.trusted_account, tracer)

            # The replayed-nonce column: the accepted cells, byte for byte,
            # Message-ID and all. The poller's idempotency store is the only
            # thing standing between these and a second execution.
            for route in ROUTES:
                deliver(cells[(route, "valid")].raw)
            replay_tracer = _build_cell("plaintext", "valid", ctx)
            deliver(replay_tracer.raw)
            _await_tracer(stack.mailserver, stack.trusted_account, replay_tracer)

            with stack.mailserver.imap_client(stack.trusted_account) as imap:
                inbox = fetch_inbox(imap)
            rows, agents = _snapshot_bus(stack.env["CHAT_DB_PATH"])
            yield Observed(
                cells=cells, anchors=anchors, inbox=inbox, rows=rows,
                agents=agents, receipts=receipts, agents_before=agents_before,
            )
        finally:
            _stack.shutdown_gpg(foreign_home)
    finally:
        stub.write_bytes(displaced)
        stub.chmod(0o700)


# ---------------------------------------------------------------------------
# Rejected column: unsigned and wrong key.
# ---------------------------------------------------------------------------

SILENT_ROUTES = ("plaintext", "thread_reply", "agent", "meta", "reaction")


@pytest.mark.parametrize("condition", REJECTED)
@pytest.mark.parametrize("route", SILENT_ROUTES)
def test_rejected_cell_produces_no_reply_and_no_bus_row(observed, route, condition):
    """An uncredentialed message on any non-JSON route is dropped in silence.

    Three independent absences are checked: no mail threaded on it, its nonce
    nowhere in the mailbox at all (so a reply that lost its threading headers
    still counts as a leak), and its nonce nowhere on the bus.

    Fails if the implementation is reverted: drop the envelope check, the GPG
    verification or the ``In-Reply-To`` lookup and the message routes normally
    — a ``[Result]``, ``[Dispatched]``, ``[Status]``, ``[Answer]`` or
    ``[Reaction]`` mail appears and, for four of the five routes, a bus row
    with it. The absence is only meaningful because a signed tracer sent
    afterwards has already completed its own full round trip.
    """
    cell = observed.cell(route, condition)
    assert observed.replies(cell) == [], (
        f"{route}/{condition} was answered: "
        f"{[m.get('Subject') for m in observed.replies(cell)]}")
    leaked = [m.get("Subject") for m in observed.inbox if cell.nonce in body_text(m)]
    assert leaked == [], f"{route}/{condition} nonce surfaced in mail: {leaked}"
    assert observed.bus_rows_matching(cell.nonce) == [], (
        f"{route}/{condition} reached the bus")


@pytest.mark.parametrize("condition", REJECTED)
def test_rejected_json_envelope_answers_unauthorized(observed, condition):
    """The JSON row answers rather than dropping — but only with an error.

    ``unsigned`` carries no ``meta.auth`` at all; ``wrong_key`` carries a
    value that is not the shared secret. Both must come back as
    ``error.code == "unauthorized"`` with no ``data``, because a
    ``list_projects`` payload would disclose the operator's project names to
    an unauthenticated caller.

    Fails if reverted: without the ``meta.auth`` comparison the reply is an
    ``ack`` carrying ``data.projects``.
    """
    cell = observed.cell("json", condition)
    replies = observed.replies(cell)
    assert len(replies) == 1, [m.get("Subject") for m in replies]
    envelope = json.loads(body_text(replies[0]))
    assert envelope.get("error", {}).get("code") == "unauthorized", envelope
    assert "data" not in envelope, envelope


def test_forged_meta_commands_never_spawned_an_agent(observed):
    """``spawn`` unsigned and ``restart`` wrong-key changed nothing.

    The strongest statement the meta row can make: the two verbs that start
    or bounce processes left the ``agents`` table exactly as this module
    found it plus the two rows the fixture seeded itself.

    Fails if reverted: an unauthenticated ``spawn`` that reached
    ``_handle_meta`` would either add an agent row or answer with a
    ``[Error]`` mail, and the previous test asserts the mail is absent.
    """
    assert set(observed.agents) == set(observed.agents_before) | {ALPHA, BETA}


# ---------------------------------------------------------------------------
# Accepted columns: valid, and stale timestamp.
#
# Both are parametrised over the same assertions on purpose. That is the
# finding: claude-email compares no timestamp on any route, so a message
# stamped 2001 behaves exactly like one stamped now. These tests are the
# evidence for that claim, and they are why the replayed-nonce column — the
# poller's Message-ID store — is the system's only temporal control.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("condition", ACCEPTED)
def test_plaintext_command_runs_and_reports_its_digest(observed, condition):
    """A signed plaintext command reaches the CLI byte for byte and replies.

    The oracle is computed from the bytes put on the wire — the SHA-256 of
    the exact command string — and never from anything the system produced.

    Fails if reverted: no envelope check or no GPG verification and the mail
    is dropped, so neither reply exists; a broken executor or mailer and the
    ``[Result]`` never carries the digest; any normalisation of the prompt
    between MIME and ``execve`` and the digest diverges.
    """
    cell = observed.cell("plaintext", condition)
    replies = observed.replies(cell)
    subjects = [m.get("Subject", "") for m in replies]
    assert sum("[Running]" in s for s in subjects) == 1, subjects
    assert sum("[Result]" in s for s in subjects) == 1, subjects
    result = next(m for m in replies if "[Result]" in m.get("Subject", ""))
    assert f"prompt-sha256: {cell.prompt_sha256}" in body_text(result)
    receipt = json.loads(
        (observed.receipts / f"{cell.prompt_sha256}.json").read_text(encoding="utf-8"))
    assert receipt["prompt"] == cell.command
    assert "--print" in receipt["argv"]


@pytest.mark.parametrize("condition", ACCEPTED)
def test_json_envelope_with_the_shared_secret_is_acked(observed, condition):
    """A correctly authenticated envelope gets a real ``list_projects`` ack.

    This is the positive control for
    :func:`test_rejected_json_envelope_answers_unauthorized` — without it,
    a handler that answered ``unauthorized`` to everything would pass the
    rejected cells and prove nothing.

    Fails if reverted: break the parser, the auth comparison in the accepting
    direction, or the JSON reply send, and there is no ``ack``.
    """
    cell = observed.cell("json", condition)
    replies = observed.replies(cell)
    assert len(replies) == 1, [m.get("Subject") for m in replies]
    envelope = json.loads(body_text(replies[0]))
    assert envelope["kind"] == "ack", envelope
    assert "error" not in envelope, envelope
    assert isinstance(envelope["data"]["projects"], list), envelope


@pytest.mark.parametrize("condition", ACCEPTED)
def test_thread_reply_is_delivered_to_the_waiting_agent(observed, condition):
    """Possession of an issued Message-ID authenticates an unsigned reply.

    The mail carries no signature and no secret. Its only credential is an
    ``In-Reply-To`` naming a Message-ID this stack itself minted when the
    relay mailed the seeded ``ask`` out — which is exactly the claim
    ``src/security.is_authorized`` makes for the chat-thread path.

    Fails if reverted: remove the ``outbound_emails`` / ``messages``
    Message-ID lookup and this unsigned mail is refused like its forged
    siblings, leaving no bus row and no ``[Answer]``.
    """
    cell = observed.cell("thread_reply", condition)
    anchor = observed.anchors[f"thread_reply_{condition}"]
    landed = [r for r in observed.bus_replies_to(anchor)
              if r["body"] == f"acknowledged {cell.nonce}"]
    assert len(landed) == 1, observed.bus_replies_to(anchor)
    assert landed[0]["to_name"] == ALPHA and landed[0]["type"] == "reply"
    replies = observed.replies(cell)
    assert len(replies) == 1 and "[Answer]" in replies[0].get("Subject", "")


@pytest.mark.parametrize("condition", ACCEPTED)
def test_agent_command_is_dispatched_to_the_named_agent(observed, condition):
    """``@agent <text>`` puts exactly one command on the bus and acks it.

    Fails if reverted: without the envelope+GPG gate this signed mail is
    indistinguishable from the unsigned one two tests up, which must produce
    nothing — so the pair can only both pass if verification actually
    discriminates. Without ``classify_email``'s ``@`` branch the mail falls
    through to the CLI and no ``command`` row is written.
    """
    cell = observed.cell("agent", condition)
    dispatched = [r for r in observed.rows
                  if r["from_name"] == "user" and r["to_name"] == ALPHA
                  and r["type"] == "command" and cell.nonce in (r["body"] or "")]
    assert len(dispatched) == 1, dispatched
    assert dispatched[0]["body"] == f"{cell.nonce} run the alpha errand"
    replies = observed.replies(cell)
    assert len(replies) == 1 and "[Dispatched]" in replies[0].get("Subject", "")


@pytest.mark.parametrize("condition", ACCEPTED)
def test_meta_status_lists_the_registered_agents(observed, condition):
    """A signed ``status`` answers with the live agent roster.

    Fails if reverted: without auth the mail is dropped (no ``[Status]``);
    without the meta branch it goes to the CLI and comes back tagged
    ``[Result]`` with the stub's digest instead of agent names.
    """
    cell = observed.cell("meta", condition)
    replies = observed.replies(cell)
    assert len(replies) == 1, [m.get("Subject") for m in replies]
    assert "[Status]" in replies[0].get("Subject", "")
    listing = body_text(replies[0])
    assert ALPHA in listing and BETA in listing, listing


@pytest.mark.parametrize("condition", ACCEPTED)
def test_reaction_answers_the_pending_ask_without_queueing_work(observed, condition):
    """A thumbs-up on a relayed ``ask`` resolves it as ``yes`` and stops there.

    Fails if reverted: without the thread credential the mail is refused and
    no row appears; without ``extract_reaction`` the body is stored verbatim
    as a reply instead of the ``yes`` the blocking ``chat_ask`` needs, and
    the ack comes back tagged ``[Answer]`` rather than ``[Reaction]``.
    """
    cell = observed.cell("reaction", condition)
    anchor = observed.anchors[f"reaction_{condition}"]
    landed = observed.bus_replies_to(anchor)
    assert len(landed) == 1, landed
    assert landed[0]["body"] == "yes" and landed[0]["to_name"] == BETA
    replies = observed.replies(cell)
    assert len(replies) == 1 and "[Reaction]" in replies[0].get("Subject", "")


# ---------------------------------------------------------------------------
# Replayed-nonce column.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("route", ROUTES)
def test_replaying_a_valid_message_has_no_second_effect(observed, route):
    """Re-delivering the accepted mail byte for byte changes nothing.

    Every ``valid`` cell was sent a second time — same Message-ID, same
    signature, same envelope — and a signed tracer afterwards proved the
    poller worked through the batch. The counts here must still be one.

    Fails if reverted: delete the ``processed_ids`` store (or stop consulting
    it in ``fetch_unseen``) and the replay executes again — a second
    ``[Result]``, a second ``ack``, a second bus row.
    """
    cell = observed.cell(route, "valid")
    assert len(observed.replies(cell)) == EXPECTED_REPLIES[route], (
        [m.get("Subject") for m in observed.replies(cell)])
    if route == "agent":
        assert len(observed.bus_rows_matching(cell.nonce)) == 1
    elif route in ("thread_reply", "reaction"):
        assert len(observed.bus_replies_to(observed.anchors[f"{route}_valid"])) == 1
    elif route == "plaintext":
        results = [m for m in observed.replies(cell)
                   if "[Result]" in m.get("Subject", "")]
        assert len(results) == 1


# ---------------------------------------------------------------------------
# The cell mocks missed: a JSON envelope against a deployment with no
# shared secret configured at all.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def secretless(stack, tmp_path_factory):
    """A real ``main.py`` booted with ``SHARED_SECRET=""`` and GPG configured.

    ``main.py``'s startup guard requires *one* of ``GPG_FINGERPRINT`` or
    ``SHARED_SECRET``, so this is a supported deployment, not a broken one.
    Everything but the mailbox, the state/DB/log paths and the secret comes
    from the session stack, so the single difference this fixture isolates is
    the one under test. It polls the ``bystander`` mailbox and so cannot race
    the session poller, and it needs no chat server: the JSON path only
    touches SQLite.
    """
    import _stack

    workdir = tmp_path_factory.mktemp("e2e-secretless")
    (workdir / "projects").mkdir()
    (workdir / "logs").mkdir()
    mailbox = stack.mailserver.accounts["bystander"]
    env = {
        **stack.env,
        "EMAIL_ADDRESS": mailbox.login, "EMAIL_PASSWORD": mailbox.password,
        "SHARED_SECRET": "",
        "CLAUDE_CWD": str(workdir / "projects"),
        "STATE_FILE": str(workdir / "processed_ids.json"),
        "LOG_FILE": str(workdir / "claude-email.log"),
        "CHAT_DB_PATH": str(workdir / "claude-chat-secretless.db"),
    }
    assert env["GPG_FINGERPRINT"], "the guard under test needs GPG still configured"
    child = _stack.spawn("poller-secretless", "main.py", env,
                         workdir / "logs", runroot=stack.runroot)
    try:
        child.wait_for_output(r"IMAP connected to \S+ as \S+")
        yield mailbox
    finally:
        child.stop()


@pytest.mark.parametrize("auth", [None, "", "anything-at-all"])
def test_json_envelope_is_rejected_when_no_secret_is_configured(stack, secretless, auth):
    """With no shared secret set, no JSON envelope may be honoured.

    Absent ``meta.auth``, empty ``meta.auth``, arbitrary ``meta.auth`` — all
    three must come back ``unauthorized``. The envelope carries no signature,
    so its only claim to authority is a ``From`` header, which any host that
    can reach the operator's MX can write.

    Fails against the pre-fix code: ``if expected and env.auth != expected``
    short-circuits on the empty secret and the reply is a ``list_projects``
    ack disclosing the operator's project names — which is exactly what this
    test returned on 2026-08-25 before ``src/json_handler.py`` was changed.
    """
    mailbox = secretless
    sender = stack.trusted_account
    nonce = os.urandom(8).hex()
    message_id = email.utils.make_msgid(domain=stack.mailserver.domain)
    meta = {"client": "e2e/1.0"}
    if auth is not None:
        meta["auth"] = auth
    send(stack.mailserver, sender, mailbox.address, json_mail(
        sender.address, mailbox.address, f"e2e no-secret {nonce}", message_id,
        {"v": 1, "kind": "list_projects", "body": nonce, "meta": meta},
    ))

    def fetch(imap, _):
        found = replies_in(fetch_inbox(imap), message_id)
        assert found, "no reply yet"
        return found

    with stack.mailserver.imap_client(sender) as imap:
        replies = stack.mailserver.wait_for(
            imap, message_id, fetch, timeout=REPLY_TIMEOUT)
    assert len(replies) == 1, [m.get("Subject") for m in replies]
    envelope = json.loads(body_text(replies[0]))
    assert envelope.get("error", {}).get("code") == "unauthorized", envelope
    assert "data" not in envelope, envelope
