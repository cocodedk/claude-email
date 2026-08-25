"""Machinery for booting the real claude-email stack against the e2e mail server.

Nothing here simulates a component of the system under test. It starts the
*actual* ``chat_server.py`` and ``main.py`` as operating-system processes, with
a purpose-built environment, and gives the tests handles for asking the kernel
and the network whether those processes are really there.

Why a TLS terminator sits in front of the mail server
-----------------------------------------------------
``src/poller.py`` and ``src/mailer.py`` speak *implicit* TLS through
``ssl.create_default_context()`` — certificate and hostname verification on,
by repo invariant. GreenMail's built-in TLS listeners present a self-signed
certificate whose subject is ``CN=GreenMail selfsigned Test Certificate`` with
no subjectAltName at all, so no client that verifies hostnames can ever accept
it, whatever CA it trusts. Reshaping the container's keystore would mean
editing ``docker-compose.yml``, which is outside this slice's declared scope.

So the harness runs the equivalent of ``stunnel``: a protocol-blind byte pump
that terminates TLS with a locally generated certificate (SAN ``127.0.0.1``)
and forwards the plaintext to GreenMail's plaintext port. It parses nothing and
answers nothing — every byte of SMTP and IMAP is spoken by the real server. The
poller's verification path stays fully armed: the child process is handed
``SSL_CERT_FILE`` pointing at the generated CA, and a connection made without
it still fails, which the tests assert as a negative control.

Why the children run from a staged run-root
-------------------------------------------
Handing a child a constructed ``env`` only pins what it is given at ``exec``.
Both entry points then *fetch* more configuration: ``load_dotenv()`` walks up
from the running module's directory for a ``.env``, and ``src/config.py`` reads
``.env.test`` from the directory two levels above its own ``__file__``. Anchored
at the real checkout, that folds the operator's ``.env`` into the child and
makes ``build_universe_resources`` create a database at the ``CHAT_DB_PATH``
their ``.env.test`` names — a write on their machine that no ``/proc`` check can
see, because it happens after ``exec``.

So the children are started from a throwaway run-root that stages the code
(:func:`stage_run_root` — packages symlinked, entry points copied, for the
reason documented there). ``os.path.abspath`` does not resolve symlinks, so both
anchors land inside the harness directory. An empty ``.env`` is planted there to
terminate the upward walk; no ``.env.test`` is planted, so the stack runs with a
single universe. A guard-that-fails on a present ``.env`` was rejected: the
operator's checkout always has one, and that would make the e2e suite unrunnable
exactly where it matters.
"""
from __future__ import annotations

import contextlib
import dataclasses
import http.client
import os
import re
import select
import shutil
import signal
import socket
import ssl
import subprocess
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"

#: Seconds to wait for a child process to reach its first working state.
BOOT_TIMEOUT = 60.0
#: Seconds to allow a child to exit on SIGTERM before SIGKILL.
STOP_GRACE = 10.0


class TlsTerminator:
    """Accept TLS on 127.0.0.1 and pump the plaintext to ``upstream``.

    Deliberately dumb: it copies bytes in both directions and has no idea
    whether it is carrying SMTP or IMAP. That is what keeps it out of the
    "mocked the thing under test" category — the protocol peer is GreenMail.
    """

    def __init__(self, upstream: tuple[str, int], certfile: str, keyfile: str) -> None:
        self._upstream = upstream
        self._ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._ctx.load_cert_chain(certfile, keyfile)
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(16)
        self.port: int = self._sock.getsockname()[1]
        self._closed = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._closed.is_set():
            try:
                raw, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(raw,), daemon=True).start()

    def _handle(self, raw: socket.socket) -> None:
        """Own one connection for its whole life, in a single thread.

        Both directions are pumped by this one thread, and that is the whole
        point. An OpenSSL ``SSL`` object is not thread-safe, and CPython's
        ``_ssl`` releases the GIL around ``SSL_read`` and ``SSL_write`` without
        taking any per-object lock. The previous design ran the two directions
        in two threads, so on every TLS session one thread sat inside
        ``SSL_read`` on the client socket while the other was inside
        ``SSL_write`` on that same socket — and under TLS 1.3 a read can itself
        write (session tickets, KeyUpdate). That corrupts the connection state:
        it surfaced as ``ssl.SSLError: [SSL] internal error`` under load, and as
        a segmentation fault that killed the whole suite, reported in whichever
        thread next touched the poisoned heap rather than in the SSL call that
        did the damage.

        Owning the connection in one thread removes the concurrency instead of
        trying to survive it. It also removes the cross-thread half-close that
        the previous fix had to work around, since the one thread that half-
        closes a direction is the one that just read its EOF.
        """
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
        # would abort an idle-but-healthy IMAP session after 30s and tear the
        # pair down mid-protocol. The relay blocks in select() instead.
        upstream.settimeout(None)
        try:
            self._relay(client, upstream)
        finally:
            for sock in (client, upstream):
                with contextlib.suppress(OSError):
                    sock.close()

    @staticmethod
    def _relay(client: ssl.SSLSocket, upstream: socket.socket) -> None:
        """Copy bytes both ways until both directions have reached EOF.

        Two details are load-bearing, both about not lying to OpenSSL:

        ``select`` cannot see data OpenSSL has already decrypted into the
        SSLSocket's own buffer. One TLS record can carry several IMAP lines, so
        a relay that waited on the file descriptor after reading part of a
        record would stall a healthy session; ``pending()`` closes that gap.

        The half-close goes through ``socket.socket.shutdown`` rather than the
        ``ssl.SSLSocket`` override, which does ``self._sslobj = None``. After
        that, ``recv`` on the still-open direction silently falls back to the
        plain socket and forwards raw ciphertext instead of the protocol. Going
        to the socket layer performs the fd-level half-close and nothing else,
        which is all the EOF propagation needs.
        """
        peers: dict = {client: upstream, upstream: client}
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
                try:
                    dst.sendall(chunk)
                except OSError:
                    peers.clear()
                    break

    def close(self) -> None:
        self._closed.set()
        with contextlib.suppress(OSError):
            self._sock.close()


def generate_tls_cert(directory: Path) -> tuple[Path, Path]:
    """Generate a self-signed cert with an IP SAN the poller can verify against."""
    cert, key = directory / "e2e-tls.crt", directory / "e2e-tls.key"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", str(key), "-out", str(cert), "-days", "2",
         "-subj", "/CN=claude-email-e2e",
         "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1"],
        capture_output=True, text=True, timeout=120, check=True,
    )
    key.chmod(0o600)
    return cert, key


def gpg(gnupghome: Path, *args: str, stdin: bytes | None = None) -> subprocess.CompletedProcess:
    """Run the real gpg binary against a throwaway home directory."""
    return subprocess.run(
        ["gpg", "--homedir", str(gnupghome), "--batch", "--yes",
         "--pinentry-mode", "loopback", *args],
        input=stdin, capture_output=True, timeout=120, check=False,
    )


def generate_gpg_key(gnupghome: Path, uid: str) -> str:
    """Create a real, passphrase-less signing key and return its fingerprint."""
    gnupghome.mkdir(parents=True, exist_ok=True)
    gnupghome.chmod(0o700)
    made = gpg(gnupghome, "--passphrase", "", "--quick-generate-key",
               uid, "default", "default", "never")
    if made.returncode != 0:
        raise RuntimeError(f"gpg key generation failed: {made.stderr.decode(errors='replace')}")
    listed = gpg(gnupghome, "--list-secret-keys", "--with-colons", uid)
    for line in listed.stdout.decode().splitlines():
        if line.startswith("fpr:"):
            return line.split(":")[9]
    raise RuntimeError(f"no fingerprint in: {listed.stdout.decode(errors='replace')}")


def shutdown_gpg(gnupghome: Path) -> None:
    """Stop the gpg-agent this home started, so the directory can be removed."""
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(["gpgconf", "--homedir", str(gnupghome), "--kill", "all"],
                       capture_output=True, timeout=30, check=False)


@dataclasses.dataclass
class Child:
    """A real OS process, its captured output, and how to kill it."""

    name: str
    proc: subprocess.Popen
    output_path: Path

    @property
    def pid(self) -> int:
        return self.proc.pid

    def is_running(self) -> bool:
        return self.proc.poll() is None

    def output(self) -> str:
        return self.output_path.read_text(errors="replace")

    def environ(self) -> dict[str, str]:
        """The child's environment as the kernel sees it — not as we meant it."""
        raw = Path(f"/proc/{self.pid}/environ").read_bytes()
        pairs = (item.split("=", 1) for item in raw.decode(errors="replace").split("\0") if "=" in item)
        return {k: v for k, v in pairs}

    def wait_for_output(self, pattern: str, timeout: float = BOOT_TIMEOUT) -> str:
        """Block until ``pattern`` appears in the child's output; fail loudly."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.is_running():
                raise AssertionError(
                    f"{self.name} exited with {self.proc.returncode} before logging "
                    f"{pattern!r}:\n{self.output()}")
            if match := re.search(pattern, self.output()):
                return match.group(0)
            time.sleep(0.2)
        raise AssertionError(
            f"{self.name} never logged {pattern!r} within {timeout}s:\n{self.output()}")

    def wait_for_path(self, path: Path, timeout: float = BOOT_TIMEOUT) -> Path:
        """Block until ``path`` exists on disk; fail loudly with the output."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.exists():
                return path
            if not self.is_running():
                raise AssertionError(
                    f"{self.name} exited with {self.proc.returncode} before "
                    f"creating {path}:\n{self.output()}")
            time.sleep(0.2)
        raise AssertionError(
            f"{self.name} never created {path} within {timeout}s:\n{self.output()}")

    def stop(self) -> None:
        """Unconditional teardown: TERM the whole process group, then KILL.

        The process group matters — the chat server supervises workers and the
        wake watcher, and a poller left alive would keep polling a mailbox.
        """
        if self.proc.poll() is None:
            with contextlib.suppress(OSError):
                os.killpg(os.getpgid(self.pid), signal.SIGTERM)
            try:
                self.proc.wait(timeout=STOP_GRACE)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(OSError):
                    os.killpg(os.getpgid(self.pid), signal.SIGKILL)
                with contextlib.suppress(subprocess.TimeoutExpired):
                    self.proc.wait(timeout=STOP_GRACE)


#: Everything a child needs from the checkout, named explicitly. An allowlist
#: rather than "copy the tree minus a few things": the whole point is that a
#: file nobody thought about — today's ``.env``, tomorrow's ``.env.local`` —
#: cannot arrive in the run-root by default.
RUN_ROOT_ENTRIES = (
    "main.py", "chat_server.py", "src", "chat", "scripts",
    ".mcp.json", ".mcp-test.json",
)


def stage_run_root(dest: Path) -> Path:
    """Stage the code into ``dest`` so config resolution anchors there.

    Directories are symlinked and top-level files are copied, and the asymmetry
    is not cosmetic. ``os.path.abspath`` — what ``_repo_root()`` and
    ``find_dotenv()`` both use — leaves symlinks unresolved, so a symlinked
    ``src`` gives ``src/config.py`` a ``__file__`` inside ``dest`` while the
    bytes executed are the repo's own. But CPython resolves the *script* path
    when it computes ``sys.path[0]``: launching a symlinked ``main.py`` puts the
    real checkout back on ``sys.path`` and ``src`` is imported from there,
    silently undoing the whole exercise. Copying the two entry-point files —
    byte-for-byte, fresh on every boot — keeps ``sys.path[0]`` pointed here.

    The planted empty ``.env`` terminates ``find_dotenv``'s upward walk; without
    it the search continues past ``dest`` into whatever lives above the temp
    directory.
    """
    dest.mkdir(parents=True, exist_ok=True)
    for entry in RUN_ROOT_ENTRIES:
        source, staged = REPO_ROOT / entry, dest / entry
        if not source.exists() or staged.exists():
            continue
        if source.is_dir():
            staged.symlink_to(source, target_is_directory=True)
        else:
            shutil.copy2(source, staged)
    (dest / ".env").touch()
    return dest


def spawn(name: str, script: str, env: dict[str, str], log_dir: Path,
          *, runroot: Path) -> Child:
    """Start ``script`` from ``runroot`` under the repo venv, in its own group."""
    out_path = log_dir / f"{name}.out"
    handle = out_path.open("wb")
    try:
        proc = subprocess.Popen(  # noqa: S603 — shell=False, fixed argv
            [str(VENV_PYTHON), str(runroot / script)],
            cwd=str(runroot), env=env, stdout=handle,
            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    finally:
        handle.close()
    return Child(name=name, proc=proc, output_path=out_path)


def write_cli_stub(path: Path) -> Path:
    """A CLAUDE_BIN that always fails loudly.

    This slice boots the stack; it does not run commands. A stub that *failed*
    silently, or succeeded, would let an accidental CLI invocation pass
    unnoticed. Exiting non-zero on stderr means any later slice that reaches
    the executor without replacing this sees it immediately.
    """
    path.write_text(
        "#!/bin/sh\n"
        "echo 'e2e stack: the claude CLI must not be invoked by this slice' >&2\n"
        "exit 97\n",
    )
    path.chmod(0o700)
    return path


def verified_ssl_context(cafile: Path | None) -> ssl.SSLContext:
    """The same verified context shape the production code builds.

    With ``cafile`` the stack's certificate is trusted; without it the default
    system store applies and the connection must fail — that pair is what shows
    verification is genuinely on.
    """
    ctx = ssl.create_default_context(cafile=str(cafile) if cafile else None)
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


def http_get(port: int, path: str, timeout: float = 15.0) -> tuple[int, dict, bytes]:
    """GET over a real socket, reading headers only for streaming endpoints.

    ``/sse`` and ``/events`` never end, so the body is read with a bounded
    ``read1`` rather than to EOF.
    """
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("GET", path)
        response = conn.getresponse()
        body = response.read1(4096) if response.headers.get(
            "content-type", "").startswith("text/event-stream") else response.read()
        return response.status, dict(response.headers), body
    finally:
        conn.close()


def wait_for_http(port: int, path: str, timeout: float = BOOT_TIMEOUT) -> None:
    """Block until the server answers ``path`` with 200."""
    deadline, problem = time.monotonic() + timeout, "never attempted"
    while time.monotonic() < deadline:
        try:
            status, _, _ = http_get(port, path, timeout=5.0)
            if status == 200:
                return
            problem = f"status {status}"
        except (OSError, http.client.HTTPException) as exc:
            problem = f"{type(exc).__name__}: {exc}"
        time.sleep(0.25)
    raise AssertionError(f"http://127.0.0.1:{port}{path} not serving — {problem}")


def free_port() -> int:
    """Reserve, then release, an ephemeral port for a child to bind."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


#: The mailbox claude-email polls, and the sender it is told to trust. Both are
#: GreenMail accounts on the ``e2e.test`` domain — no real address appears here.
POLLED_ACCOUNT = "recipient"
TRUSTED_ACCOUNT = "sender"


def build_stack_env(mailserver, *, imaps_port: int, smtps_port: int, chat_port: int,
                    cafile: Path, gnupghome: Path, fingerprint: str,
                    workdir: Path, shared_secret: str) -> dict[str, str]:
    """The child processes' entire environment — constructed, never inherited.

    Built explicitly rather than from a copy of ``os.environ`` because the
    worst outcome this harness can produce is a poller that inherits the
    operator's real IMAP credentials and starts consuming their mailbox. A
    child that is missing a variable fails loudly; a child that silently
    inherits one does not.

    The keys are the ones ``.env.example`` documents, so the real
    ``build_config`` / ``build_universes`` seam runs unmodified inside the real
    ``main.py``. This dict covers only the exec-time channel; the other one —
    ``load_dotenv()`` and ``build_config``'s ``.env.test`` lookup, both of which
    happen after exec and neither of which reads this dict — is closed by
    running the children from a staged run-root instead. See the module
    docstring and :func:`stage_run_root`.
    """
    polled = mailserver.accounts[POLLED_ACCOUNT]
    trusted = mailserver.accounts[TRUSTED_ACCOUNT]
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(workdir / "home"),
        "LANG": "C.UTF-8",
        "PYTHONUNBUFFERED": "1",
        # Trust the terminator's certificate — and nothing else that is not
        # already in the system store. Read by ssl.create_default_context().
        "SSL_CERT_FILE": str(cafile),
        "IMAP_HOST": mailserver.host, "IMAP_PORT": str(imaps_port),
        "SMTP_HOST": mailserver.host, "SMTP_PORT": str(smtps_port),
        # GreenMail authenticates on the bare local part; the full address is
        # rejected as a bad credential (see the Account docstring in conftest).
        "EMAIL_ADDRESS": polled.login, "EMAIL_PASSWORD": polled.password,
        "AUTHORIZED_SENDER": trusted.address, "EMAIL_DOMAIN": mailserver.domain,
        "POLL_INTERVAL": "1", "CLAUDE_TIMEOUT": "30",
        "CLAUDE_BIN": str(write_cli_stub(workdir / "claude-stub")),
        "CLAUDE_CWD": str(workdir / "projects"),
        "STATE_FILE": str(workdir / "processed_ids.json"),
        "LOG_FILE": str(workdir / "claude-email.log"),
        "CHAT_DB_PATH": str(workdir / "claude-chat-e2e.db"),
        "CHAT_HOST": "127.0.0.1", "CHAT_PORT": str(chat_port),
        "CHAT_URL": f"http://127.0.0.1:{chat_port}/sse",
        "SERVICE_NAME_EMAIL": "claude-email-e2e.service",
        "SERVICE_NAME_CHAT": "claude-chat-e2e.service",
        "SHARED_SECRET": shared_secret,
        "GPG_FINGERPRINT": fingerprint, "GPG_HOME": str(gnupghome),
        "GNUPGHOME": str(gnupghome),
        # The wake watcher spawns CLI turns for dormant agents. Nothing is
        # registered on a fresh DB, and CLAUDE_BIN refuses to run anyway.
        "WAKE_WATCHER_INTERVAL_SECS": "5.0",
        "WAKE_SUBPROCESS_TIMEOUT_SECS": "30",
        "WAKE_USER_AVATAR_NAME": "user",
    }
    # Run the production seam over this dict now, so a shape mistake surfaces
    # here rather than as an opaque child-process traceback.
    from src.universes import build_universes
    build_universes(env, test_env=None)
    return env


@dataclasses.dataclass
class Stack:
    """Everything a later slice needs to drive the booted system."""

    mailserver: object
    env: dict[str, str]
    workdir: Path
    runroot: Path
    cafile: Path
    gnupghome: Path
    gpg_fingerprint: str
    gpg_uid: str
    shared_secret: str
    imaps_port: int
    smtps_port: int
    chat_port: int
    chat: Child
    poller: Child

    @property
    def polled_account(self):
        return self.mailserver.accounts[POLLED_ACCOUNT]

    @property
    def trusted_account(self):
        return self.mailserver.accounts[TRUSTED_ACCOUNT]

    def gpg(self, *args: str, stdin: bytes | None = None) -> subprocess.CompletedProcess:
        return gpg(self.gnupghome, *args, stdin=stdin)


def missing_tooling() -> str | None:
    """Return why the stack cannot be booted here, or ``None``."""
    for tool in ("gpg", "gpgconf", "openssl"):
        if shutil.which(tool) is None:
            return f"{tool} executable not found on PATH"
    if not VENV_PYTHON.exists():
        return f"repo venv interpreter missing at {VENV_PYTHON}"
    return None


@contextlib.contextmanager
def boot_stack(mailserver, workdir: Path):
    """Boot the whole stack and guarantee it is torn down again.

    Teardown is unconditional and runs in reverse dependency order even if
    *any* step of the boot raised: the poller first (it is the component that
    talks to a mailbox), then the chat server, then the TLS terminators, then
    the gpg-agent holding the throwaway keyring open. ExitStack unwinds in
    reverse registration order, so registering the poller's stop() last is
    what puts it first — and a leaked poller, still consuming a mailbox after
    the suite ends, is the single worst failure this harness could produce.
    """
    (workdir / "home").mkdir(parents=True, exist_ok=True)
    (workdir / "projects").mkdir(parents=True, exist_ok=True)
    logs = workdir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    runroot = stage_run_root(workdir / "run-root")
    gnupghome = workdir / "gnupg"
    gpg_uid = f"claude-email e2e <e2e-gpg@{mailserver.domain}>"
    shared_secret = os.urandom(24).hex()

    with contextlib.ExitStack() as stack:
        stack.callback(shutdown_gpg, gnupghome)
        fingerprint = generate_gpg_key(gnupghome, gpg_uid)

        cert, key = generate_tls_cert(workdir)
        imaps = TlsTerminator((mailserver.host, mailserver.imap_port), str(cert), str(key))
        stack.callback(imaps.close)
        smtps = TlsTerminator((mailserver.host, mailserver.smtp_port), str(cert), str(key))
        stack.callback(smtps.close)

        chat_port = free_port()
        env = build_stack_env(
            mailserver, imaps_port=imaps.port, smtps_port=smtps.port,
            chat_port=chat_port, cafile=cert, gnupghome=gnupghome,
            fingerprint=fingerprint, workdir=workdir, shared_secret=shared_secret,
        )

        chat = spawn("chat_server", "chat_server.py", env, logs, runroot=runroot)
        stack.callback(chat.stop)
        poller = spawn("poller", "main.py", env, logs, runroot=runroot)
        stack.callback(poller.stop)

        wait_for_http(chat_port, "/api/agents")
        poller.wait_for_output(r"IMAP connected to \S+ as \S+")

        yield Stack(
            mailserver=mailserver, env=env, workdir=workdir, runroot=runroot,
            cafile=cert,
            gnupghome=gnupghome, gpg_fingerprint=fingerprint, gpg_uid=gpg_uid,
            shared_secret=shared_secret, imaps_port=imaps.port,
            smtps_port=smtps.port, chat_port=chat_port, chat=chat, poller=poller,
        )
