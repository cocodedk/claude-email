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
    """Connection details for the running server."""

    host: str
    smtp_port: int
    imap_port: int
    smtps_port: int
    imaps_port: int
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


def _compose(*args: str, timeout: float) -> subprocess.CompletedProcess:
    """Run a docker compose subcommand for this project (shell=False)."""
    return subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "-p", COMPOSE_PROJECT, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
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
def mailserver():
    """Start the real mail server for the session and tear it down after."""
    reason = _docker_unavailable_reason()
    if reason is not None:
        pytest.skip(f"e2e mail server needs docker — {reason}")

    smtp_port, imap_port = _port("SMTP", 13025), _port("IMAP", 13143)
    smtps_port, imaps_port = _port("SMTPS", 13465), _port("IMAPS", 13993)

    try:
        # A first run may have to pull the image, hence the long timeout.
        up = _compose("up", "-d", "--remove-orphans", timeout=STARTUP_TIMEOUT)
        if up.returncode != 0:
            pytest.fail(f"docker compose up failed:\n{up.stdout}\n{up.stderr}")
        # The TLS listeners (smtps_port/imaps_port) come up in the same
        # GreenMail startup as these two and are published for later slices;
        # gating on the two plaintext greetings is what proves that startup
        # finished.
        _await_banners({smtp_port: b"220", imap_port: b"* OK"}, timeout=90.0)
        yield MailServer(HOST, smtp_port, imap_port, smtps_port, imaps_port)
    finally:
        _compose("down", "-v", "--remove-orphans", timeout=120.0)


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
