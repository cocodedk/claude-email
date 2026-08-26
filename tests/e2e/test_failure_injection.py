"""Break the real dependencies mid-flight and watch what the system does.

Nothing here is patched. Every failure is inflicted on a *real* object:

* ``docker compose kill`` on a real GreenMail container, mid-poll;
* ``SIGKILL`` on a real ``src.project_worker`` process and the real CLI child
  it had launched, mid-task;
* a real TCP severance of the poller's live IMAP session at the instant it
  issues ``UID … FETCH``;
* ``SIGKILL`` on a real ``main.py`` in the window between accepting a command
  and executing it.

Each test asserts the *documented* outcome, and the point of the module is
that two of those outcomes are unflattering.

The at-most-once trade, stated honestly
---------------------------------------
``src/poller.py`` fetches with ``UID FETCH … (RFC822)``. RFC 3501 makes a
non-``.PEEK`` body fetch implicitly set ``\\Seen`` on the server, and GreenMail
does exactly that — the harness verified it against the live server before this
module was written, and :func:`test_imap_fetch_sets_seen_server_side` re-verifies
it here so the claim is not folklore.

``main.py`` only calls ``poller.mark_processed`` *after* dispatch, so the
persisted idempotency store is written late. The combination means the delivery
guarantee is **at most once**:

* the command is never executed twice on this path — a crash cannot cause a
  re-run, because the server already considers the message read;
* a crash between the fetch and the execution therefore *loses* the command.

The loss is not silent in the sense that matters for an operator: three durable
artefacts survive the crash and each one is asserted below — the message is
still sitting in the mailbox (now ``\\Seen``, with no reply threaded to it), its
Message-ID is absent from ``STATE_FILE`` so nothing claims it was handled, and
the execution ledger has no entry for it. What the *user* gets is nothing at
all: if the kill lands while the ``[Running]`` acknowledgement is still on the
wire, no mail ever reaches them. That is the trade, and the docs say so.

Why this module runs its own mail server
----------------------------------------
Injection A stops the mail server. GreenMail keeps mailboxes in memory, so
stopping it destroys every message on the host — which would sabotage the
session-scoped ``stack`` fixture and every other e2e module. This module
therefore boots a *second* GreenMail under its own compose project name,
container name and ephemeral ports, and each test gets its own poller, ledger,
state file and database. Blast radius: zero.

Independent oracles
-------------------
Execution counts come from an append-only ledger written by the CLI stand-in —
a third-party program outside the SUT, reached by a real fork/exec. Mail is read
back off a real IMAP socket. Task rows are read read-only from outside the
writing process. Process liveness comes from ``/proc`` and ``waitpid``, not from
anything the SUT reports about itself.

Where the TLS in this module comes from
--------------------------------------
Every ordinary path here goes straight to GreenMail's own IMAPS and SMTPS
listeners, verified: the fixture below generates a throwaway SAN keypair, hands
the private half to the container in a PKCS12 keystore and the public half to
the pollers as ``SSL_CERT_FILE``. There is no transport shim.

Injection C is the exception, and it is the exception on purpose. Severing a
live IMAP session *before* GreenMail executes the ``FETCH`` — the whole point,
because a fetch the server executed would have set ``\\Seen`` and the command
would be gone — requires seeing the command as it is issued, which means
standing on the wire. :class:`FetchSeveringProxy` is that, and its blast radius
is exactly one fixture method and one test: it is built by
``Cell.severing_imaps()``, which only
``test_severing_the_imap_connection_mid_fetch_loses_no_command`` calls, and only
that test's poller is pointed at its port. Injections A, B and D reach
GreenMail's own verified IMAPS and SMTPS listeners like every other e2e module.
Everything the proxy does not sever it forwards untouched, so the protocol peer
is still the real server. The shared harness has no such object;
``test_tls_direct.py`` asserts that across the whole directory and exempts this
file by name.

Scope note
----------
The private mail server is described by a local dataclass with the same shape
``_stack.build_stack_env`` expects.
"""
from __future__ import annotations

import contextlib
import dataclasses
import email
import email.utils
import json
import os
import signal
import select
import smtplib
import socket
import sqlite3
import ssl
import subprocess
import threading
import time
from pathlib import Path

import pytest

import _stack

#: Must match ``-Dgreenmail.users`` in ``tests/e2e/docker-compose.yml``.
DOMAIN = "e2e.test"
HOST = "127.0.0.1"
COMPOSE_FILE = Path(__file__).parent / "docker-compose.yml"

#: Seconds to allow the private container to start serving.
SERVER_TIMEOUT = 180.0
#: Seconds to allow a reply to come back through the whole stack.
REPLY_TIMEOUT = 180.0
#: After the system has done the right thing, give it this long to do the
#: wrong one before believing the absence.
SETTLE_SECONDS = 8.0
#: Poll interval configured on every poller in this module.
POLL_INTERVAL = 1


# ---------------------------------------------------------------------------
# The CLI stand-in. Two ledger lines per execution — one before the work and
# one after — so "started but never finished" is distinguishable from "never
# started", which is precisely the distinction injection B turns on.
# ---------------------------------------------------------------------------

STUB_SOURCE = '''#!/usr/bin/env python3
"""Execution-recording stand-in for the claude CLI (test_failure_injection)."""
import json
import os
import sys
import time

argv = sys.argv[1:]
if "--print" not in argv:
    sys.stderr.write("e2e failure-injection stub: no --print in argv: %r\\n" % (argv,))
    raise SystemExit(3)
prompt = argv[argv.index("--print") + 1]
ledger = os.environ["E2E_FAILURE_LEDGER"]


def record(phase):
    with open(ledger, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(
            {"phase": phase, "prompt": prompt, "pid": os.getpid()}) + "\\n")
        handle.flush()
        os.fsync(handle.fileno())


record("start")
time.sleep(float(os.environ.get("E2E_STUB_SLEEP", "0")))
record("end")
sys.stdout.write("E2E-FAILURE-INJECTION-EXECUTED\\n" + prompt + "\\n")
'''


def read_ledger(path: Path, nonce: str = "") -> list[dict]:
    """Every recorded CLI phase, optionally narrowed to one nonce."""
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [r for r in rows if not nonce or nonce in r["prompt"]]


def phases(path: Path, nonce: str, phase: str) -> list[dict]:
    return [r for r in read_ledger(path, nonce) if r["phase"] == phase]


# ---------------------------------------------------------------------------
# A private mail server: same image and same compose file, different project,
# container and ports, so it can be killed without touching the session one.
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Account:
    login: str
    password: str

    @property
    def address(self) -> str:
        return f"{self.login}@{DOMAIN}"


ACCOUNTS = {
    "sender": Account("e2e-sender", "sender-pw"),
    "recipient": Account("e2e-recipient", "recipient-pw"),
    "bystander": Account("e2e-bystander", "bystander-pw"),
}


@dataclasses.dataclass
class PrivateServer:
    """Connection details for this module's own GreenMail, plus its kill switch.

    The attribute names ``host``, ``domain`` and ``accounts`` are the contract
    ``_stack.build_stack_env`` reads; the rest is local.
    """

    host: str
    domain: str
    accounts: dict
    smtp_port: int
    imap_port: int
    smtps_port: int
    imaps_port: int
    #: Public half of the keypair whose private half this container's keystore
    #: holds — both the certificate its TLS listeners serve and the only CA the
    #: pollers in this module are told to trust.
    cafile: Path
    keyfile: Path
    project: str
    container: str
    compose_env: dict

    def _compose(self, *args: str, timeout: float) -> subprocess.CompletedProcess:
        return subprocess.run(  # noqa: S603 — shell=False, fixed argv
            ["docker", "compose", "-f", str(COMPOSE_FILE), "-p", self.project, *args],
            capture_output=True, text=True, timeout=timeout,
            check=False, env=self.compose_env,
        )

    def kill_container(self) -> None:
        """``docker compose kill`` — SIGKILL the JVM, no graceful shutdown."""
        killed = self._compose("kill", timeout=120.0)
        assert killed.returncode == 0, f"docker compose kill failed: {killed.stderr}"

    def start_container(self) -> None:
        started = self._compose("start", timeout=SERVER_TIMEOUT)
        assert started.returncode == 0, f"docker compose start failed: {started.stderr}"
        self.await_serving()

    def await_serving(self, timeout: float = SERVER_TIMEOUT) -> None:
        await_banner(self.smtp_port, b"220", timeout)
        await_banner(self.imap_port, b"* OK", timeout)
        # These two are what src/mailer.py and src/poller.py talk to here, so
        # readiness has to mean "serving the harness certificate", not merely
        # "accepting" — otherwise the pollers race the JVM's listener bind.
        _stack.await_tls_greeting(HOST, self.smtps_port, b"220", self.cafile, timeout)
        _stack.await_tls_greeting(HOST, self.imaps_port, b"* OK", self.cafile, timeout)

    def unreachable(self) -> bool:
        """True when a plain TCP+greeting probe of the IMAP port fails."""
        try:
            return not read_banner(self.imap_port).startswith(b"* OK")
        except OSError:
            return True

    @contextlib.contextmanager
    def imap_client(self, account: Account):
        import imaplib
        conn = imaplib.IMAP4(self.host, self.imap_port)
        try:
            conn.login(account.login, account.password)
            yield conn
        finally:
            with contextlib.suppress(Exception):
                conn.logout()


def read_banner(port: int, timeout: float = 5.0) -> bytes:
    with socket.create_connection((HOST, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        with sock.makefile("rb") as stream:
            return stream.readline()


def await_banner(port: int, prefix: bytes, timeout: float) -> None:
    """Block until the port answers with its protocol greeting.

    Docker's userland proxy accepts connections long before the JVM binds, so
    "can I connect?" is not a readiness signal — the greeting is.
    """
    deadline, problem = time.monotonic() + timeout, "never attempted"
    while time.monotonic() < deadline:
        try:
            banner = read_banner(port)
            if banner.startswith(prefix):
                return
            problem = f"greeting {banner!r} did not start with {prefix!r}"
        except OSError as exc:
            problem = f"connection failed: {exc}"
        time.sleep(0.5)
    raise AssertionError(f"mail server not serving on {HOST}:{port} — {problem}")


def docker_unavailable() -> str | None:
    import shutil
    if shutil.which("docker") is None:
        return "docker executable not found on PATH"
    try:
        info = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                              capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"could not run 'docker info': {exc}"
    if info.returncode != 0:
        return f"docker daemon not reachable: {info.stderr.strip()}"
    return None


# ---------------------------------------------------------------------------
# The fault injector for injection C. See "Where the TLS in this module comes
# from" in the module docstring for why this one object terminates TLS.
# ---------------------------------------------------------------------------

class FetchSeveringProxy:
    """Front an IMAP server with TLS and cut one session on its first ``FETCH``.

    Severance happens *before* the command is forwarded, so GreenMail never
    executes it and never sets ``\\Seen``: the message survives the failure and
    the poller must recover it. Triggering is one-shot and only while armed;
    every other byte in both directions is carried through untouched.

    One thread owns a connection for its whole life, and that is load-bearing.
    An OpenSSL ``SSL`` object is not thread-safe and CPython's ``_ssl`` releases
    the GIL around ``SSL_read``/``SSL_write`` without taking a per-object lock,
    so pumping the two directions from two threads puts one inside ``SSL_read``
    on the same socket another is writing — and under TLS 1.3 a read can itself
    write (session tickets, KeyUpdate). That corrupted the connection state in
    an earlier design, surfacing as ``SSLError: internal error`` under load and
    as a segfault reported in whichever thread next touched the poisoned heap.
    Owning the connection in one thread removes the concurrency rather than
    trying to survive it.
    """

    def __init__(self, upstream: tuple[str, int], certfile: str, keyfile: str) -> None:
        self._upstream = upstream
        self._ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._ctx.load_cert_chain(certfile, keyfile)
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((HOST, 0))
        self._sock.listen(16)
        self.port: int = self._sock.getsockname()[1]
        self.armed = threading.Event()
        self.tripped = threading.Event()
        self._closed = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while not self._closed.is_set():
            try:
                raw, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(raw,), daemon=True).start()

    def _handle(self, raw: socket.socket) -> None:
        try:
            client = self._ctx.wrap_socket(raw, server_side=True)
        except OSError:
            raw.close()
            return
        try:
            upstream = socket.create_connection(self._upstream, timeout=30)
        except OSError:
            client.close()
            return
        # create_connection leaves its connect timeout on the socket, which
        # would abort an idle-but-healthy IMAP session after 30s. The relay
        # blocks in select() instead.
        upstream.settimeout(None)
        try:
            self._relay(client, upstream)
        finally:
            for sock in (client, upstream):
                with contextlib.suppress(OSError):
                    sock.close()

    def _relay(self, client: ssl.SSLSocket, upstream: socket.socket) -> None:
        """Copy both ways until EOF, or until an armed ``FETCH`` goes past.

        Two details are about not lying to OpenSSL. ``select`` cannot see data
        OpenSSL has already decrypted into the SSLSocket's own buffer, and one
        TLS record can carry several IMAP lines, so ``pending()`` closes that
        gap. And the half-close goes through ``socket.socket.shutdown`` rather
        than ``ssl.SSLSocket``'s override, which sets ``self._sslobj = None`` —
        after which ``recv`` on the still-open direction silently forwards raw
        ciphertext instead of the protocol.

        Severance is a plain ``return``: the caller's ``finally`` closes both
        sockets, so the command never reaches GreenMail and the poller sees its
        session die. Client-side bytes are accumulated rather than matched per
        read, so a command split across two records still trips it.
        """
        peers: dict = {client: upstream, upstream: client}
        issued = bytearray()
        while peers:
            ready, _, _ = select.select(list(peers), [], [], 1.0)
            if client in peers and client.pending():
                ready = [client, *(s for s in ready if s is not client)]
            for src in ready:
                if src not in peers:
                    continue
                dst = peers[src]
                try:
                    chunk = src.recv(65536)
                except OSError:
                    chunk = b""
                if not chunk:
                    with contextlib.suppress(OSError):
                        socket.socket.shutdown(dst, socket.SHUT_WR)
                    del peers[src]
                    continue
                if src is client and self.armed.is_set() and not self.tripped.is_set():
                    issued += chunk
                    if b"FETCH" in issued.upper():
                        self.tripped.set()
                        return
                try:
                    dst.sendall(chunk)
                except OSError:
                    return

    def close(self) -> None:
        self._closed.set()
        with contextlib.suppress(OSError):
            self._sock.close()


class SmtpGate:
    """A TCP listener that swallows the first connection and reports it.

    The poller reaches SMTP exactly once per accepted command — the
    ``[Running]`` acknowledgement ``main.process_email`` sends immediately
    after the command has been authenticated and extracted, and immediately
    *before* ``execute_command`` forks the CLI. Accepting that connection and
    never answering therefore pins the process in the accept→execute window,
    which is the window injection D needs to kill in.

    Later connections are forwarded to ``upstream`` so a poller left running
    behind the gate is not permanently mute. That forwarding is ciphertext-
    blind — ``upstream`` is GreenMail's own SMTPS listener and this object never
    terminates TLS, so the poller still verifies the container's certificate
    end to end and nothing here can see or alter a byte of SMTP.
    """

    def __init__(self, upstream: tuple[str, int]) -> None:
        self._upstream = upstream
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((HOST, 0))
        self._sock.listen(16)
        self.port: int = self._sock.getsockname()[1]
        self.accepted = threading.Event()
        self._held: list[socket.socket] = []
        self._closed = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while not self._closed.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            if not self.accepted.is_set():
                self._held.append(conn)
                self.accepted.set()
                continue
            threading.Thread(target=self._forward, args=(conn,), daemon=True).start()

    def _forward(self, conn: socket.socket) -> None:
        try:
            upstream = socket.create_connection(self._upstream, timeout=30)
        except OSError:
            conn.close()
            return
        upstream.settimeout(None)
        pumps = [threading.Thread(target=pump, args=(a, b), daemon=True)
                 for a, b in ((conn, upstream), (upstream, conn))]
        for pump in pumps:
            pump.start()
        for pump in pumps:
            pump.join()
        for sock in (conn, upstream):
            with contextlib.suppress(OSError):
                sock.close()

    def close(self) -> None:
        self._closed.set()
        for sock in [*self._held, self._sock]:
            with contextlib.suppress(OSError):
                sock.close()


# ---------------------------------------------------------------------------
# Wire-format construction, by hand. Nothing borrows the production serialiser.
# ---------------------------------------------------------------------------

def raw_mail(sender: str, recipient: str, subject: str, message_id: str,
             body: str, content_type: str = "text/plain; charset=utf-8") -> bytes:
    head = "\r\n".join((
        f"From: {sender}", f"To: {recipient}", f"Subject: {subject}",
        f"Message-ID: {message_id}",
        f"Date: {email.utils.formatdate(localtime=False)}",
        "MIME-Version: 1.0",
        f"Content-Type: {content_type}",
        "Content-Transfer-Encoding: 8bit",
    ))
    return head.encode("utf-8") + b"\r\n\r\n" + body.encode("utf-8") + b"\r\n"


def send_mail(server: PrivateServer, account: Account, recipient: str,
              raw: bytes) -> None:
    with smtplib.SMTP(server.host, server.smtp_port, timeout=30) as smtp:
        smtp.login(account.login, account.password)
        refused = smtp.sendmail(account.address, [recipient], raw)
    assert refused == {}, f"SMTP refused recipients: {refused}"


# ---------------------------------------------------------------------------
# Reading the outside world back.
# ---------------------------------------------------------------------------

def _literals(fetched) -> list[bytes]:
    return [part[1] for part in fetched
            if isinstance(part, tuple) and isinstance(part[1], (bytes, bytearray))]


def inbox(imap) -> list:
    """Every message in INBOX, fetched with BODY.PEEK so no flag is disturbed."""
    status, _ = imap.select("INBOX")
    assert status == "OK", f"IMAP SELECT failed: {status}"
    status, data = imap.search(None, "ALL")
    assert status == "OK", f"IMAP SEARCH failed: {status}"
    out = []
    for uid in data[0].split():
        status, fetched = imap.fetch(uid, "(BODY.PEEK[])")
        assert status == "OK", f"IMAP FETCH failed: {status}"
        parts = _literals(fetched)
        if parts:
            out.append(email.message_from_bytes(parts[0]))
    return out


def body_text(message) -> str:
    chunks = []
    for part in message.walk():
        if part.is_multipart():
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        chunks.append(payload.decode(part.get_content_charset() or "utf-8",
                                     errors="replace"))
    return "\n".join(chunks)


def replies_for(server: PrivateServer, nonce: str) -> list:
    """Outbound mail carrying ``nonce``, read from the trusted sender's inbox."""
    with server.imap_client(server.accounts["sender"]) as imap:
        return [m for m in inbox(imap)
                if nonce in (m.get("Subject", "") + body_text(m))]


def await_reply(server: PrivateServer, nonce: str, needle: str,
                timeout: float = REPLY_TIMEOUT) -> list:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = [m for m in replies_for(server, nonce)
                 if needle in (m.get("Subject", "") + body_text(m))]
        if found:
            return found
        time.sleep(1.0)
    raise AssertionError(f"no reply containing {needle!r} for {nonce} within {timeout}s")


def flag_state(server: PrivateServer, message_id: str) -> str:
    """``seen`` / ``unseen`` / ``absent`` for one Message-ID in the polled box."""
    with server.imap_client(server.accounts["recipient"]) as imap:
        imap.select("INBOX")
        for label, criterion in (("seen", "SEEN"), ("unseen", "UNSEEN")):
            status, data = imap.search(None, criterion)
            assert status == "OK"
            for num in data[0].split():
                status, fetched = imap.fetch(num, "(BODY.PEEK[HEADER])")
                assert status == "OK"
                parts = _literals(fetched)
                if parts and message_id in parts[0].decode(errors="replace"):
                    return label
    return "absent"


def processed_ids(state_file: Path) -> list[str]:
    if not state_file.exists():
        return []
    return json.loads(state_file.read_text())


def db_rows(db_path: Path, sql: str, args: tuple = ()) -> list[dict]:
    """Read-only snapshot of a real SQLite file, from outside the writer."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


def pid_alive(pid: int) -> bool:
    """Kernel truth, and a zombie does not count as alive."""
    try:
        state = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return state.rsplit(") ", 1)[-1].split(" ", 1)[0] not in ("Z", "X")


def kill_now(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.kill(pid, signal.SIGKILL)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def private_server(tmp_path_factory):
    """A GreenMail all to this module, safe to kill.

    The keystore is built before ``compose up`` and bind-mounted in, because
    GreenMail reads it once at startup: its certificate is what this
    container's IMAPS and SMTPS listeners serve, and the same file is the CA
    every poller in this module is given.
    """
    reason = docker_unavailable()
    if reason is not None:
        pytest.skip(f"failure injection needs docker — {reason}")

    tls_reason = _stack.missing_tooling()
    if tls_reason is not None:
        pytest.skip(f"failure injection needs local tooling — {tls_reason}")

    ports = {name: _stack.free_port() for name in ("SMTP", "IMAP", "SMTPS", "IMAPS")}
    project = f"claude-email-e2e-fail-{os.getpid()}"
    container = f"{project}-mailserver"
    tlsdir = tmp_path_factory.mktemp("e2e-failure-tls")
    cert, key = _stack.generate_tls_cert(tlsdir)
    keystore = _stack.make_keystore(cert, key, tlsdir / "e2e-keystore.p12")
    compose_env = {
        **os.environ,
        "CLAUDE_EMAIL_E2E_CONTAINER": container,
        "CLAUDE_EMAIL_E2E_KEYSTORE": str(keystore),
        "CLAUDE_EMAIL_E2E_KEYSTORE_PASSWORD": _stack.KEYSTORE_PASSWORD,
        **{f"CLAUDE_EMAIL_E2E_{name}_PORT": str(port) for name, port in ports.items()},
    }
    server = PrivateServer(
        host=HOST, domain=DOMAIN, accounts=dict(ACCOUNTS),
        smtp_port=ports["SMTP"], imap_port=ports["IMAP"],
        smtps_port=ports["SMTPS"], imaps_port=ports["IMAPS"],
        cafile=cert, keyfile=key,
        project=project, container=container, compose_env=compose_env,
    )
    try:
        up = server._compose("up", "-d", "--remove-orphans", timeout=SERVER_TIMEOUT)
        if up.returncode != 0:
            pytest.fail(f"private mail server failed to start:\n{up.stdout}\n{up.stderr}")
        server.await_serving()
        yield server
    finally:
        server._compose("down", "-v", "--remove-orphans", timeout=120.0)


@pytest.fixture(scope="module")
def lab(private_server, tmp_path_factory):
    """Staged code, shared by every test in the module.

    ``cert``/``key`` are the container's own keypair, not a second one: the
    pollers verify GreenMail directly against ``cert``, and
    :class:`FetchSeveringProxy` presents the same certificate so the one
    connection it stands on verifies against the same CA as every other.
    """
    root = tmp_path_factory.mktemp("e2e-failure")
    runroot = _stack.stage_run_root(root / "run-root")
    return {"root": root, "runroot": runroot,
            "cert": private_server.cafile, "key": private_server.keyfile,
            "server": private_server}


class Cell:
    """One test's private slice of the world: env, children, ledger.

    By default *both* transports go straight to the container's own verified
    listeners — ``self.server.imaps_port`` and ``self.server.smtps_port``. The
    severing proxy is not built here and is not in the path of any test that
    does not ask for it: injection C asks, with :meth:`severing_imaps`, and
    starts its poller against that port explicitly. Three of the four tests in
    this module therefore have no TLS terminator anywhere near them.
    """

    def __init__(self, lab, workdir: Path) -> None:
        self.lab = lab
        self.server: PrivateServer = lab["server"]
        self.workdir = workdir
        self._imaps: FetchSeveringProxy | None = None
        self.ledger = workdir / "ledger.jsonl"
        self.state_file = workdir / "processed_ids.json"
        self.db_path = workdir / "claude-chat-failure.db"
        self.children: list[_stack.Child] = []
        # Deliberately not ``claude-stub``: ``_stack.build_stack_env`` writes
        # its own refusing stub to that name in this same directory, and would
        # overwrite the recording one below.
        self.cli = workdir / "e2e-recording-cli"
        self.env = self._build_env()
        self.cli.write_text(STUB_SOURCE)
        self.cli.chmod(0o700)

    def _build_env(self) -> dict:
        gnupghome = self.workdir / "gnupg"
        gnupghome.mkdir(mode=0o700, exist_ok=True)
        env = _stack.build_stack_env(
            self.server, imaps_port=self.server.imaps_port,
            smtps_port=self.server.smtps_port,
            chat_port=_stack.free_port(), cafile=self.lab["cert"],
            gnupghome=gnupghome, fingerprint="", workdir=self.workdir,
            shared_secret=os.urandom(24).hex(),
        )
        return {
            **env,
            # Bearer-token deployment: no GPG anywhere in this module, so the
            # shared-secret and JSON-envelope routes are the reachable ones and
            # main.py's "one of the two" startup guard is satisfied by the
            # secret. Nothing here is about authentication.
            "GPG_FINGERPRINT": "",
            "POLL_INTERVAL": str(POLL_INTERVAL),
            "CLAUDE_BIN": str(self.cli),
            "CLAUDE_CWD": str(self.workdir / "projects"),
            "STATE_FILE": str(self.state_file),
            "LOG_FILE": str(self.workdir / "claude-email.log"),
            "CHAT_DB_PATH": str(self.db_path),
            "E2E_FAILURE_LEDGER": str(self.ledger),
            "E2E_STUB_SLEEP": "0",
            # Long enough that a worker is still idling when a test looks for
            # it, short enough that nothing survives the module.
            "WORKER_IDLE_TIMEOUT": "60",
            "WORKER_TASK_TIMEOUT": "120",
        }

    @property
    def secret(self) -> str:
        return self.env["SHARED_SECRET"]

    def severing_imaps(self) -> FetchSeveringProxy:
        """Build this cell's one severing proxy — injection C only.

        Deliberately not built in the fixture. A proxy constructed per cell
        would sit in the IMAPS path of every test that takes ``cell``, three of
        which have nothing to do with severing a session: they would run the
        real poller's LOGIN and its ``AUTH:<secret>`` body through a
        pytest-owned TLS terminator and out to the cleartext IMAP port for no
        reason. The caller passes ``IMAP_PORT=str(proxy.port)`` to
        :meth:`start_poller`, so the exception's blast radius is one poller in
        one test.
        """
        assert self._imaps is None, "a cell gets at most one severing proxy"
        self._imaps = FetchSeveringProxy(
            (self.server.host, self.server.imap_port),
            str(self.lab["cert"]), str(self.lab["key"]))
        return self._imaps

    def start_poller(self, name: str, **overrides) -> _stack.Child:
        env = {**self.env, **overrides}
        child = _stack.spawn(name, "main.py", env, self.workdir / "logs",
                             runroot=self.lab["runroot"])
        self.children.append(child)
        child.wait_for_output(r"IMAP connected to \S+ as \S+")
        return child

    def send(self, subject: str, message_id: str, body: str,
             content_type: str = "text/plain; charset=utf-8") -> None:
        accounts = self.server.accounts
        send_mail(self.server, accounts["sender"], accounts["recipient"].address,
                  raw_mail(accounts["sender"].address,
                           accounts["recipient"].address, subject, message_id,
                           body, content_type))

    def message_id(self, tag: str) -> str:
        return f"<fail-{tag}-{os.urandom(6).hex()}@{DOMAIN}>"

    def stop(self) -> None:
        for child in reversed(self.children):
            child.stop()
        if self._imaps is not None:
            self._imaps.close()


@pytest.fixture
def cell(lab, tmp_path):
    """Per-test isolation — a poller left over from one test cannot reach the next."""
    workdir = tmp_path / "cell"
    for sub in ("logs", "projects", "home"):
        (workdir / sub).mkdir(parents=True, exist_ok=True)
    made = Cell(lab, workdir)
    try:
        yield made
    finally:
        made.stop()


def send_tracer(cell: Cell, poller: _stack.Child, tag: str) -> str:
    """Drive one ordinary command through and prove the poller is awake.

    Returns the nonce. Used after every recovery so that an assertion about
    something *not* happening cannot be satisfied by a dead poller.
    """
    nonce = f"{tag}-{os.urandom(6).hex()}"
    cell.send(f"AUTH:{cell.secret} e2e tracer {nonce}",
              cell.message_id(tag), f"e2e tracer {nonce}: echo this line back.")
    await_reply(cell.server, nonce, "[Result]")
    assert poller.is_running(), f"poller died after tracer {nonce}"
    return nonce


def await_envelope(server: PrivateServer, nonce: str, kind: str,
                   timeout: float = REPLY_TIMEOUT) -> dict:
    """Wait for a JSON reply of ``kind`` on the thread carrying ``nonce``."""
    deadline, seen = time.monotonic() + timeout, []
    while time.monotonic() < deadline:
        for message in replies_for(server, nonce):
            try:
                envelope = json.loads(body_text(message))
            except ValueError:
                continue
            seen.append(envelope.get("kind"))
            if envelope.get("kind") == kind:
                return envelope
        time.sleep(1.0)
    raise AssertionError(f"no {kind!r} envelope for {nonce} in {timeout}s; saw {seen}")


def count_in_output(child: _stack.Child, needle: str) -> int:
    return child.output().count(needle)


def await_count(child: _stack.Child, needle: str, wanted: int,
                timeout: float = 60.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = count_in_output(child, needle)
        if found >= wanted:
            return found
        if not child.is_running():
            break
        time.sleep(0.5)
    raise AssertionError(
        f"{child.name} logged {needle!r} {count_in_output(child, needle)}× "
        f"in {timeout}s, wanted {wanted}:\n{child.output()[-4000:]}")


# ---------------------------------------------------------------------------
# The premise the whole at-most-once argument rests on.
# ---------------------------------------------------------------------------

def test_imap_fetch_sets_seen_server_side(private_server):
    """``UID FETCH (RFC822)`` — what ``src/poller.py`` issues — marks the mail read.

    This is the reason the crash window in
    :func:`test_sigkill_between_accepting_and_executing_loses_the_command` is
    unrecoverable, and it is asserted against the live server rather than
    quoted from RFC 3501, because the guarantee that matters is the one this
    deployment's server actually implements.

    Fails if reverted: nothing in ``src/`` can make it pass or fail — it pins a
    property of the dependency. If a future mail server, or a switch to
    ``BODY.PEEK`` in the poller, changed that property, this test is what tells
    the next reader the loss analysis has to be redone.
    """
    box = private_server.accounts["bystander"]
    nonce = os.urandom(8).hex()
    mid = f"<seen-probe-{nonce}@{DOMAIN}>"
    send_mail(private_server, private_server.accounts["sender"], box.address,
              raw_mail(private_server.accounts["sender"].address, box.address,
                       f"seen probe {nonce}", mid, "probe"))

    with private_server.imap_client(box) as imap:
        deadline = time.monotonic() + 60
        uid = None
        while time.monotonic() < deadline:
            imap.select("INBOX")
            status, data = imap.uid("SEARCH", None, "UNSEEN")
            assert status == "OK"
            for candidate in data[0].split():
                _, fetched = imap.uid("FETCH", candidate, "(BODY.PEEK[HEADER])")
                parts = _literals(fetched)
                if parts and mid in parts[0].decode(errors="replace"):
                    uid = candidate
                    break
            if uid:
                break
            time.sleep(0.5)
        assert uid is not None, f"probe {mid} never arrived unseen"

        status, _ = imap.uid("FETCH", uid, "(RFC822)")
        assert status == "OK"

        imap.select("INBOX")
        status, unseen = imap.uid("SEARCH", None, "UNSEEN")
        assert status == "OK"
        assert uid not in unseen[0].split(), (
            "the probe is still UNSEEN after a non-PEEK fetch — the poller's "
            "read is no longer destructive and the at-most-once analysis in "
            "this module's docstring needs revisiting")


# ---------------------------------------------------------------------------
# A — stop the mail server mid-poll.
# ---------------------------------------------------------------------------

def test_killing_the_mail_server_mid_poll_does_not_kill_the_poller(cell):
    """SIGKILL the real GreenMail container underneath a live poller.

    Documented outcome (``main.run_loop``): the IMAP failure is caught, logged
    as ``IMAP error — retrying``, and the loop keeps going. The poller must not
    exit, must not spin its idempotency store, and must resume the moment the
    dependency comes back.

    Fails if reverted: delete ``run_loop``'s ``except Exception`` around the
    poll block and the first failed ``connect()`` propagates out of the loop —
    the process exits and both the liveness assertion and the post-restart
    tracer fail. Weaken the retry to a single attempt and the tracer never
    executes.
    """
    poller = cell.start_poller("poller-outage")
    before = send_tracer(cell, poller, "pre-outage")
    assert len(phases(cell.ledger, before, "end")) == 1

    cell.server.kill_container()
    try:
        assert cell.server.unreachable(), "the container survived docker compose kill"

        # Two logged failures means the loop went round at least twice with the
        # dependency dead — one could be a single exception on the way out.
        assert await_count(poller, "IMAP error", 2, timeout=60.0) >= 2
        assert poller.is_running(), (
            f"poller exited during the outage:\n{poller.output()[-4000:]}")
        assert pid_alive(poller.pid)
    finally:
        # The container is module-scoped. Restart it whatever happened above,
        # or a failure here cascades into every later injection as a mail
        # server that is simply not running.
        cell.server.start_container()
    after = send_tracer(cell, poller, "post-outage")
    assert len(phases(cell.ledger, after, "start")) == 1
    assert len(phases(cell.ledger, after, "end")) == 1
    assert len(await_reply(cell.server, after, "[Result]")) == 1


# ---------------------------------------------------------------------------
# B — SIGKILL the worker mid-task.
# ---------------------------------------------------------------------------

def test_sigkilling_a_worker_mid_task_is_reported_not_silently_dropped(cell):
    """Kill a real ``src.project_worker`` and its real CLI child, mid-task.

    Documented outcome (``src/ghost_reaper.py``): the task row is left
    ``running`` with a dead pid, the next housekeeping tick reaps it, marks it
    ``failed`` with ``worker exited unexpectedly``, and ``notify_task_done``
    puts a message on the bus which the relay sends to the user as mail. The
    work is *not* retried — that is the at-most-once half — but it is also not
    lost silently: the row, the log line and the mail all say so.

    Fails if reverted: remove the ``sweep_ghosts`` call from
    ``main._tick_housekeeping`` and the row stays ``running`` forever, so the
    status assertion times out and no result envelope is ever mailed. Remove
    the ``notify_task_done`` call from the worker's ``_finish``/reaper path and
    the mail assertion fails while the row assertion still passes.
    """
    project_name = "e2e-failure-project"
    (cell.workdir / "projects" / project_name).mkdir(parents=True, exist_ok=True)
    # The stub holds the task open long enough to be killed mid-flight. It is
    # the CLI, not the worker: the worker itself is real, unmodified code.
    poller = cell.start_poller("poller-worker", E2E_STUB_SLEEP="180")

    nonce = os.urandom(8).hex()
    subject = f"e2e worker kill {nonce}"
    payload = {
        "v": 1, "kind": "command", "project": project_name,
        "body": f"e2e worker kill {nonce}: hold this task open.",
        "meta": {"client": "e2e/1.0", "auth": cell.secret,
                 "sent_at": email.utils.format_datetime(
                     email.utils.parsedate_to_datetime(
                         email.utils.formatdate(localtime=False)))},
    }
    cell.send(subject, cell.message_id("worker"), json.dumps(payload),
              "application/json; charset=utf-8")

    ack = await_envelope(cell.server, nonce, "ack")
    task_id, worker_pid = ack["task_id"], ack["data"]["worker_pid"]
    assert pid_alive(worker_pid), f"ack named worker pid {worker_pid}, already gone"

    # Wait until the CLI is genuinely running under that worker — the task is
    # now mid-flight, which is the only moment worth killing in.
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline and not phases(cell.ledger, nonce, "start"):
        time.sleep(0.5)
    started = phases(cell.ledger, nonce, "start")
    assert len(started) == 1, f"the task never reached the CLI: {read_ledger(cell.ledger)}"
    assert not phases(cell.ledger, nonce, "end")

    rows = db_rows(cell.db_path, "SELECT * FROM tasks WHERE id = ?", (task_id,))
    assert len(rows) == 1 and rows[0]["status"] == "running", rows
    cli_pid = rows[0]["pid"]
    assert cli_pid == started[0]["pid"], (
        f"queue recorded pid {cli_pid}, the CLI reports {started[0]['pid']}")

    kill_now(worker_pid)
    kill_now(cli_pid)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and (pid_alive(worker_pid) or pid_alive(cli_pid)):
        time.sleep(0.2)
    assert not pid_alive(worker_pid) and not pid_alive(cli_pid)

    # The reaper runs once per poll tick; give it several.
    deadline, row = time.monotonic() + 120, {}
    while time.monotonic() < deadline:
        row = db_rows(cell.db_path, "SELECT * FROM tasks WHERE id = ?", (task_id,))[0]
        if row["status"] != "running":
            break
        time.sleep(1.0)
    assert row["status"] == "failed", f"ghost task never reaped: {row}"
    assert "worker exited unexpectedly" in (row["error_text"] or ""), row
    assert "ghost reaper" in poller.output()

    result = await_envelope(cell.server, nonce, "result")
    assert result["data"]["status"] == "failed", result
    assert "worker exited unexpectedly" in json.dumps(result), result

    # At most once: nothing re-ran the killed task.
    time.sleep(SETTLE_SECONDS)
    assert len(phases(cell.ledger, nonce, "start")) == 1, read_ledger(cell.ledger)
    assert not phases(cell.ledger, nonce, "end")
    assert db_rows(cell.db_path,
                   "SELECT id FROM tasks WHERE project_path LIKE ?",
                   (f"%{project_name}",)) == [{"id": task_id}]


# ---------------------------------------------------------------------------
# C — drop the IMAP connection mid-fetch.
# ---------------------------------------------------------------------------

def test_severing_the_imap_connection_mid_fetch_loses_no_command(cell):
    """Cut the live IMAP session at the instant the poller issues ``FETCH``.

    The severance happens before the command reaches GreenMail, so the server
    never marks the message read. Documented outcome: the poller logs the IMAP
    error, retries on the next tick, and the command is executed exactly once —
    the recoverable half of the failure spectrum, and the reason the loss in
    injection D is about *when* the crash lands, not about crashes as such.

    Fails if reverted: delete ``run_loop``'s ``except Exception`` and the
    process dies on the severed socket instead of retrying, so the ``[Result]``
    never arrives. Make ``mark_processed`` unconditional at fetch time instead
    of after dispatch and the state-file assertion below still passes, but the
    duplicate-execution assertion is what would catch a retry that re-ran it.
    """
    # The one poller in this module that does not go straight to GreenMail's
    # own IMAPS listener. Built here rather than in the fixture so the other
    # three injections have no terminator in their path — see
    # ``Cell.severing_imaps``.
    severing = cell.severing_imaps()
    poller = cell.start_poller("poller-sever", IMAP_PORT=str(severing.port))
    send_tracer(cell, poller, "pre-sever")
    errors_before = count_in_output(poller, "IMAP error")

    # The polled mailbox is empty and fully read at this point, so the poller
    # issues no FETCH at all until the message below lands: arming now makes
    # the trip and the command the same event.
    severing.armed.set()
    nonce = os.urandom(8).hex()
    message_id = cell.message_id("sever")
    cell.send(f"AUTH:{cell.secret} e2e sever {nonce}", message_id,
              f"e2e sever {nonce}: echo this line back.")

    assert severing.tripped.wait(timeout=120), (
        "the poller never issued a FETCH to sever:\n" + poller.output()[-4000:])
    assert await_count(poller, "IMAP error", errors_before + 1, timeout=60.0)

    assert len(await_reply(cell.server, nonce, "[Result]")) == 1
    time.sleep(SETTLE_SECONDS)
    assert len(phases(cell.ledger, nonce, "start")) == 1, read_ledger(cell.ledger)
    assert len(phases(cell.ledger, nonce, "end")) == 1
    assert len(replies_for(cell.server, nonce)) == 2, (
        "expected exactly one [Running] and one [Result]")
    assert message_id in processed_ids(cell.state_file)
    assert flag_state(cell.server, message_id) == "seen"
    assert poller.is_running()


# ---------------------------------------------------------------------------
# D — SIGKILL the poller between accepting a command and executing it.
# ---------------------------------------------------------------------------

def test_sigkill_between_accepting_and_executing_loses_the_command(cell):
    """The accepted trade, asserted rather than asserted-away.

    The gate pins the poller in the accept→execute window: ``process_email``
    has authenticated the sender, extracted the command, and opened SMTP for
    the ``[Running]`` acknowledgement, and has not yet forked the CLI. SIGKILL
    lands there.

    Documented outcome — **at most once, and this is the "zero" case**. The
    server already set ``\\Seen`` when the poller fetched the message, so a
    restarted poller never sees it again and the command is *never executed*.
    It is not executed twice either, which is the guarantee the design trades
    for: no crash on this path can duplicate an effect.

    Nor is the loss untraceable. Three durable artefacts are asserted here:
    the message is still in the mailbox and flagged ``\\Seen``; its Message-ID
    is absent from ``STATE_FILE``, so nothing in the system claims it was
    handled; and the ledger has no entry for it. What is *not* claimed — and
    must not be — is that the user was told: the acknowledgement died on the
    wire with the process, so their mailbox stays empty. That asymmetry is the
    honest statement of the trade.

    Fails if reverted: move ``poller.mark_processed`` before dispatch and the
    Message-ID turns up in the state file. Switch the poller to ``BODY.PEEK``
    and the message stays ``UNSEEN``, gets re-fetched by the restarted poller,
    and both the flag assertion and the never-executed assertion fail — which
    is the correct signal, because the delivery guarantee would then have
    changed and the docs would be wrong.
    """
    gate = SmtpGate((HOST, cell.server.smtps_port))
    try:
        poller = cell.start_poller("poller-gated", SMTP_PORT=str(gate.port))
        nonce = os.urandom(8).hex()
        message_id = cell.message_id("lost")
        cell.send(f"AUTH:{cell.secret} e2e lost {nonce}", message_id,
                  f"e2e lost {nonce}: echo this line back.")

        assert gate.accepted.wait(timeout=180), (
            "the poller never reached SMTP, so it never accepted the command:\n"
            + poller.output()[-4000:])
        # Accepted, acknowledgement in flight, CLI not yet forked.
        assert not phases(cell.ledger, nonce, "start"), read_ledger(cell.ledger)

        kill_now(poller.pid)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and poller.is_running():
            time.sleep(0.2)
        assert not poller.is_running(), "the poller survived SIGKILL"
        assert poller.proc.returncode == -signal.SIGKILL
    finally:
        gate.close()

    restarted = cell.start_poller("poller-restarted")
    tracer = send_tracer(cell, restarted, "after-loss")
    time.sleep(SETTLE_SECONDS)

    # At most once — and here that count is zero.
    assert read_ledger(cell.ledger, nonce) == [], (
        "the lost command executed after all: " + repr(read_ledger(cell.ledger)))
    # The trace the operator can follow.
    assert flag_state(cell.server, message_id) == "seen"
    assert message_id not in processed_ids(cell.state_file), (
        "the state file claims the lost command was processed")
    # And what the user got: nothing. Documented, not papered over.
    assert replies_for(cell.server, nonce) == []
    # The tracer proves the restarted poller was awake for all of the above.
    assert len(phases(cell.ledger, tracer, "end")) == 1
    assert restarted.is_running()
