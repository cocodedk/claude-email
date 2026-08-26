"""Machinery for booting the real claude-email stack against the e2e mail server.

Nothing here simulates a component of the system under test. It starts the
*actual* ``chat_server.py`` and ``main.py`` as operating-system processes, with
a purpose-built environment, and gives the tests handles for asking the kernel
and the network whether those processes are really there.

How the children reach TLS
--------------------------
``src/poller.py`` and ``src/mailer.py`` speak *implicit* TLS through
``ssl.create_default_context()`` — certificate and hostname verification on,
by repo invariant — and they now speak it straight to GreenMail's own IMAPS and
SMTPS listeners. Nothing sits in the path.

That needs one thing from the container, because GreenMail's bundled keystore
holds a certificate whose subject is ``CN=GreenMail selfsigned Test
Certificate`` with no subjectAltName at all, and CPython has required a SAN for
hostname verification since 3.7 — so no verifying client can ever accept it,
whatever CA it trusts. :func:`generate_tls_cert` and :func:`make_keystore` build
a throwaway keypair with SAN ``127.0.0.1`` + ``localhost`` and wrap it in a
PKCS12 keystore, which ``docker-compose.yml`` bind-mounts and names via
``-Dgreenmail.tls.keystore.file``. The children are handed the public half as
``SSL_CERT_FILE``; a connection made without it still fails, which the tests
assert as a negative control.

The harness used to answer this with a hand-rolled TLS terminator instead — a
protocol-blind byte pump in front of GreenMail's cleartext ports. It cost two
crash fixes (a segfault on connection teardown, a two-thread OpenSSL race)
before it stopped killing the suite, and it is deleted. The only TLS server
socket left in this directory is the one-shot fault injector in
``test_failure_injection.py``, where severing a live session *is* the failure
under test.

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
import shutil
import signal
import socket
import ssl
import subprocess
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"

#: Seconds to wait for a child process to reach its first working state.
BOOT_TIMEOUT = 60.0
#: Seconds to allow a child to exit on SIGTERM before SIGKILL.
STOP_GRACE = 10.0


def generate_tls_cert(directory: Path) -> tuple[Path, Path]:
    """Generate a self-signed cert with an IP SAN the poller can verify against.

    Self-signed deliberately: the certificate GreenMail serves and the CA the
    children trust are then the same file, so ``test_tls_direct.py`` can assert
    the DER on the wire is byte-identical to ``SSL_CERT_FILE`` — which is what
    rules out something having terminated TLS in between with a key of its own.
    """
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


#: Protects a keypair that is generated fresh per session and thrown away with
#: the temp directory. It reaches the container on GreenMail's command line, so
#: it is a literal rather than a secret: there is nothing here worth hiding.
KEYSTORE_PASSWORD = "e2e-throwaway-keystore"


def make_keystore(cert: Path, key: Path, destination: Path) -> Path:
    """Pack ``cert``/``key`` into the PKCS12 keystore GreenMail is pointed at.

    PKCS12 rather than JKS because ``KeyStore.getDefaultType()`` has been
    ``pkcs12`` since JDK 9 and GreenMail's ``DummySSLServerSocketFactory`` asks
    for the default type. World-readable because the JVM inside the container
    runs as a different uid and the bind mount preserves the host mode; the
    private key it protects exists only for this session.
    """
    subprocess.run(
        ["openssl", "pkcs12", "-export", "-inkey", str(key), "-in", str(cert),
         "-name", "greenmail", "-out", str(destination),
         "-passout", f"pass:{KEYSTORE_PASSWORD}"],
        capture_output=True, text=True, timeout=120, check=True,
    )
    destination.chmod(0o644)
    return destination


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


def await_tls_greeting(host: str, port: int, prefix: bytes, cafile: Path,
                       timeout: float = BOOT_TIMEOUT) -> None:
    """Block until ``port`` answers a *verified* handshake with its greeting.

    Readiness for the listeners ``src/poller.py`` and ``src/mailer.py`` talk to
    has to mean "serving the certificate we expect", not "accepting
    connections": docker publishes a port through a userland proxy that accepts
    from the moment the container is created, well before the JVM has bound
    anything, and a keystore GreenMail failed to load would still accept and
    then fail the handshake. Requiring the greeting *through* a verified session
    is the only signal that covers both, and it surfaces a keystore problem here
    — naming the port — rather than as a child that never logs that it
    connected.
    """
    ctx = verified_ssl_context(cafile)
    deadline, problem = time.monotonic() + timeout, "never attempted"
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=5) as raw, \
                    ctx.wrap_socket(raw, server_hostname=host) as tls:
                tls.settimeout(5)
                with tls.makefile("rb") as stream:
                    greeting = stream.readline()
            if greeting.startswith(prefix):
                return
            problem = f"greeting {greeting!r} did not start with {prefix!r}"
        except (OSError, ssl.SSLError) as exc:
            problem = f"{type(exc).__name__}: {exc}"
        time.sleep(0.5)
    raise AssertionError(
        f"not serving verified TLS on {host}:{port} — {problem}")


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
        # Trust the certificate GreenMail serves from the harness keystore —
        # and nothing else that is not already in the system store. Read by
        # ssl.create_default_context() inside the child.
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
    talks to a mailbox), then the chat server, then the gpg-agent holding the
    throwaway keyring open. ExitStack unwinds in
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

        # The children go straight to the container's own TLS listeners, which
        # serve the certificate the ``mailserver`` fixture generated and handed
        # GreenMail in a keystore before it started. Nothing to set up here and
        # nothing to tear down — that is the point of the terminator's deletion.
        chat_port = free_port()
        env = build_stack_env(
            mailserver, imaps_port=mailserver.imaps_port,
            smtps_port=mailserver.smtps_port,
            chat_port=chat_port, cafile=mailserver.cafile, gnupghome=gnupghome,
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
            cafile=mailserver.cafile,
            gnupghome=gnupghome, gpg_fingerprint=fingerprint, gpg_uid=gpg_uid,
            shared_secret=shared_secret, imaps_port=mailserver.imaps_port,
            smtps_port=mailserver.smtps_port, chat_port=chat_port,
            chat=chat, poller=poller,
        )
