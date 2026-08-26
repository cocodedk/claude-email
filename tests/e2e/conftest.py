"""Folder-scoped fixtures for the e2e suite — a real mail server, no mocks.

Convention note: the repo keeps shared fixtures in underscore-prefixed helper
modules and has exactly one root conftest (which holds no fixtures). A
folder-scoped conftest is the sanctioned exception for a directory that needs a
session-scoped resource plus collection hooks — see ``tests/json_handler``.

Everything in this directory is marked ``e2e`` automatically, so the default
suite can exclude it with ``-m "not e2e"`` and CI can opt in with ``-m e2e``.
When docker is unavailable the whole directory skips with the specific reason.
"""
from __future__ import annotations

import contextlib
import dataclasses
import imaplib
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

COMPOSE_FILE = Path(__file__).parent / "docker-compose.yml"
#: Unique project name — this machine runs other compose projects.
COMPOSE_PROJECT = "claude-email-e2e"
HOST = "127.0.0.1"
DOMAIN = "e2e.test"

#: Seconds to allow for ``up -d``; the first run may have to pull the image.
STARTUP_TIMEOUT = 300.0
#: Seconds to allow for a message to land in a mailbox.
DELIVERY_TIMEOUT = 30.0


def _port(name: str, default: int) -> int:
    return int(os.environ.get(f"CLAUDE_EMAIL_E2E_{name}_PORT", default))


@dataclasses.dataclass(frozen=True)
class Account:
    """A mailbox on the e2e server.

    ``login`` is the bare local part, not the address: GreenMail authenticates
    on the local part alone, and passing the full address is rejected as a bad
    credential. Later slices configuring claude-email need this distinction.
    """

    login: str
    password: str

    @property
    def address(self) -> str:
        return f"{self.login}@{DOMAIN}"


#: Must match ``-Dgreenmail.users`` in docker-compose.yml.
ACCOUNTS = {
    "sender": Account("e2e-sender", "sender-pw"),
    "recipient": Account("e2e-recipient", "recipient-pw"),
    "bystander": Account("e2e-bystander", "bystander-pw"),
}


@dataclasses.dataclass(frozen=True)
class MailServer:
    """Connection details for the running server.

    ``cafile`` is the public half of the throwaway keypair whose private half
    the container holds in its keystore, so it is both the certificate the TLS
    listeners serve and the only certificate the children are told to trust.
    """

    host: str
    smtp_port: int
    imap_port: int
    smtps_port: int
    imaps_port: int
    cafile: Path
    domain: str = DOMAIN
    accounts: dict = dataclasses.field(default_factory=lambda: dict(ACCOUNTS))

    @contextlib.contextmanager
    def imap_client(self, account: Account):
        """A freshly authenticated IMAP connection, closed on exit."""
        conn = imaplib.IMAP4(self.host, self.imap_port)
        try:
            conn.login(account.login, account.password)
            yield conn
        finally:
            with contextlib.suppress(Exception):
                conn.logout()

    def wait_for(self, imap, nonce: str, fetch, timeout: float = DELIVERY_TIMEOUT):
        """Poll ``fetch(imap, nonce)`` until delivery completes.

        SMTP delivery is asynchronous, so the first SEARCH can legitimately come
        back empty. Only an actual timeout is a failure; the assertion inside
        ``fetch`` is what decides correctness.
        """
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            try:
                return fetch(imap, nonce)
            except AssertionError as exc:  # not yet delivered
                last = exc
                time.sleep(0.25)
        raise AssertionError(f"message {nonce} never arrived within {timeout}s: {last}")


def _compose(*args: str, timeout: float,
             env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Run a docker compose subcommand for this project (shell=False).

    ``env`` is layered over the inherited environment rather than replacing it:
    compose needs ``PATH``, ``HOME`` and the docker context variables, and the
    keystore path and password the file interpolates are added on top. Both are
    written ``${VAR:?...}`` in the compose file, so a caller that forgets them
    gets a refusal naming the variable instead of a silent misconfiguration.
    """
    return subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "-p", COMPOSE_PROJECT, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env={**os.environ, **(env or {})},
    )


def _docker_unavailable_reason() -> str | None:
    """Return why docker cannot be used here, or ``None`` if it can."""
    if shutil.which("docker") is None:
        return "docker executable not found on PATH"
    try:
        probe = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"could not run 'docker compose version': {exc}"
    if probe.returncode != 0:
        return f"'docker compose' unavailable: {probe.stderr.strip() or probe.stdout.strip()}"
    try:
        info = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True, text=True, timeout=60, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"could not run 'docker info': {exc}"
    if info.returncode != 0:
        return f"docker daemon not reachable: {info.stderr.strip() or info.stdout.strip()}"
    return None


def _read_banner(port: int) -> bytes:
    """Open a socket, return the server's greeting line (may raise OSError)."""
    with socket.create_connection((HOST, port), timeout=5) as sock:
        sock.settimeout(5)
        with sock.makefile("rb") as stream:
            return stream.readline()


def _await_banners(expected: dict[int, bytes], timeout: float) -> None:
    """Block until each port answers with its protocol greeting.

    A plain "can I connect?" check is not enough: docker publishes the port via
    a userland proxy that accepts connections from the moment the container is
    created, well before the JVM inside has bound anything. Such a connection is
    accepted and then immediately closed, which reads as "ready" and fails the
    first real command. Requiring the actual SMTP/IMAP greeting is the only
    readiness signal that means the server is serving.
    """
    deadline = time.monotonic() + timeout
    for port, prefix in expected.items():
        while True:
            try:
                banner = _read_banner(port)
                if banner.startswith(prefix):
                    break
                problem = f"greeting was {banner!r}, expected it to start with {prefix!r}"
            except OSError as exc:
                problem = f"connection failed: {exc}"
            if time.monotonic() >= deadline:
                raise AssertionError(
                    f"mail server not serving on {HOST}:{port} — {problem}"
                )
            time.sleep(0.5)


@pytest.fixture(scope="session")
def mailserver(tmp_path_factory):
    """Start the real mail server for the session and tear it down after.

    The keystore is built *before* ``compose up`` and bind-mounted in, because
    GreenMail reads it once at startup. Its certificate is what the container's
    IMAPS and SMTPS listeners serve, and its public half is the only CA the
    child processes are given — see ``_stack.boot_stack``.
    """
    reason = _docker_unavailable_reason()
    if reason is not None:
        pytest.skip(f"e2e mail server needs docker — {reason}")

    # Imported here, not at module scope: pytest's prepend import mode puts this
    # directory on sys.path while importing this conftest, and the helper is a
    # top-level module rather than a package member (see the ``stack`` fixture).
    import _stack

    if shutil.which("openssl") is None:
        pytest.skip("e2e mail server needs openssl to build its TLS keystore")

    smtp_port, imap_port = _port("SMTP", 13025), _port("IMAP", 13143)
    smtps_port, imaps_port = _port("SMTPS", 13465), _port("IMAPS", 13993)

    tlsdir = tmp_path_factory.mktemp("e2e-mailserver-tls")
    cert, key = _stack.generate_tls_cert(tlsdir)
    keystore = _stack.make_keystore(cert, key, tlsdir / "e2e-keystore.p12")
    compose_env = {
        "CLAUDE_EMAIL_E2E_KEYSTORE": str(keystore),
        "CLAUDE_EMAIL_E2E_KEYSTORE_PASSWORD": _stack.KEYSTORE_PASSWORD,
    }

    try:
        # A first run may have to pull the image, hence the long timeout.
        up = _compose("up", "-d", "--remove-orphans",
                      timeout=STARTUP_TIMEOUT, env=compose_env)
        if up.returncode != 0:
            pytest.fail(f"docker compose up failed:\n{up.stdout}\n{up.stderr}")
        _await_banners({smtp_port: b"220", imap_port: b"* OK"}, timeout=90.0)
        # These two are the listeners the children actually talk to, so they
        # are gated on a verified handshake rather than a bare connect.
        for port, greeting in ((smtps_port, b"220"), (imaps_port, b"* OK")):
            _stack.await_tls_greeting(HOST, port, greeting, cert, timeout=90.0)
        yield MailServer(HOST, smtp_port, imap_port, smtps_port, imaps_port, cert)
    finally:
        _compose("down", "-v", "--remove-orphans", timeout=120.0, env=compose_env)


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "e2e: real end-to-end test; needs docker and a live mail server"
    )


def pytest_collection_modifyitems(items):
    """Mark everything collected from this directory as ``e2e``."""
    here = Path(__file__).parent
    for item in items:
        if here in Path(str(item.fspath)).parents:
            item.add_marker(pytest.mark.e2e)


@pytest.fixture(scope="session")
def stack(mailserver, tmp_path_factory):
    """The whole system, running: mail server, GPG keyring, chat bus, poller.

    Session-scoped because booting real processes costs seconds and every
    later e2e slice wants the same live stack. Everything it creates lives
    under a pytest tmp directory and is torn down unconditionally — see
    ``_stack.boot_stack`` for the ordering and why it matters.
    """
    # ``tests/e2e`` has no ``__init__.py``, so pytest's prepend import mode puts
    # this directory on sys.path and the helper is a top-level module. The
    # repo's ``from tests._x import ...`` form would need a new package file,
    # which is outside this slice's declared scope.
    import _stack

    reason = _stack.missing_tooling()
    if reason is not None:
        pytest.skip(f"e2e stack needs local tooling — {reason}")

    workdir = tmp_path_factory.mktemp("e2e-stack")
    with _stack.boot_stack(mailserver, workdir) as booted:
        yield booted
