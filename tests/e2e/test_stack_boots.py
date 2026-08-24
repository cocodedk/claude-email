"""The whole system really boots: mail server, GPG, chat bus, poller.

Every assertion here interrogates something outside the test process — a TCP
socket, an HTTP response, ``/proc``, the gpg binary, a child process's exit
status. Nothing is patched, and no assertion rests on a value produced by the
code it is judging: the fingerprint is read back out of gpg, the mail server's
identity is established by a TLS handshake that verifies a certificate, and the
children's environment is checked against a literal declared in this file plus
the pytest process's own ``os.environ`` — never against the harness dict alone,
which would widen in lockstep with the very regression it should catch.

Isolation from the operator's machine is tested through two separate channels,
because it leaks through two. ``/proc/<pid>/environ`` covers only what the
children were handed at ``exec``; it is a snapshot and says nothing about
configuration a process fetches for itself afterwards. The run-root test covers
that second channel — ``load_dotenv()`` and ``build_config``'s ``.env.test``
lookup, both of which run after ``exec`` and resolve paths from ``__file__``.
Neither test implies the other.

Each test would fail if the harness were reverted, because each one asks a
question that only a genuinely running component can answer.
"""
import imaplib
import json
import os
import shutil
import smtplib
import socket
import ssl
import subprocess

import pytest

import _stack

#: Every variable the children are allowed to have, written out here rather
#: than derived from ``_stack.build_stack_env`` — an oracle produced by the
#: function under test cannot detect that function widening. Keep in sync by
#: hand; ``test_child_processes_cannot_reach_the_operators_mailbox`` fails
#: loudly in both directions if this drifts.
EXPECTED_CHILD_KEYS = {
    "PATH", "HOME", "LANG", "PYTHONUNBUFFERED", "SSL_CERT_FILE",
    "IMAP_HOST", "IMAP_PORT", "SMTP_HOST", "SMTP_PORT",
    "EMAIL_ADDRESS", "EMAIL_PASSWORD", "AUTHORIZED_SENDER", "EMAIL_DOMAIN",
    "POLL_INTERVAL", "CLAUDE_TIMEOUT", "CLAUDE_BIN", "CLAUDE_CWD",
    "STATE_FILE", "LOG_FILE", "CHAT_DB_PATH", "CHAT_HOST", "CHAT_PORT",
    "CHAT_URL", "SERVICE_NAME_EMAIL", "SERVICE_NAME_CHAT",
    "SHARED_SECRET", "GPG_FINGERPRINT", "GPG_HOME", "GNUPGHOME",
    "WAKE_WATCHER_INTERVAL_SECS", "WAKE_SUBPROCESS_TIMEOUT_SECS",
    "WAKE_USER_AVATAR_NAME",
}


def test_mail_server_answers_verified_tls_on_both_transports(stack):
    """IMAPS and SMTPS both complete a *verified* TLS handshake and log in.

    The negative control is the point: with the stack's CA the handshake
    succeeds, and without it the very same endpoint is rejected. A harness
    that had quietly disabled verification would pass the first half and fail
    the second, so the pair proves the poller's verified-TLS path is real
    rather than merely configured.
    """
    account = stack.polled_account
    trusted = _stack.verified_ssl_context(stack.cafile)

    with imaplib.IMAP4_SSL("127.0.0.1", stack.imaps_port, ssl_context=trusted) as imap:
        assert imap.login(account.login, account.password)[0] == "OK"
        assert imap.select("INBOX")[0] == "OK"

    with smtplib.SMTP_SSL("127.0.0.1", stack.smtps_port, context=trusted, timeout=30) as smtp:
        assert smtp.noop()[0] == 250
        smtp.login(account.login, account.password)

    with pytest.raises(ssl.SSLCertVerificationError):
        imaplib.IMAP4_SSL(
            "127.0.0.1", stack.imaps_port,
            ssl_context=_stack.verified_ssl_context(None),
        )


def test_gnupghome_is_throwaway_and_holds_a_real_usable_key(stack):
    """The generated key exists in the temp home, signs, and verifies.

    Sign-then-verify of a fresh nonce is the independent oracle: a directory
    containing files that merely look like a keyring cannot produce a
    signature that gpg itself accepts.
    """
    assert stack.gnupghome.is_dir()
    assert str(stack.gnupghome).startswith("/tmp") or "pytest" in str(stack.gnupghome)

    listed = stack.gpg("--list-secret-keys", "--with-colons").stdout.decode()
    assert f"fpr:::::::::{stack.gpg_fingerprint}:" in listed

    nonce = os.urandom(16).hex().encode()
    signed = stack.gpg("--armor", "--detach-sign", "--local-user",
                       stack.gpg_fingerprint, "-o", "-", stdin=nonce)
    assert signed.returncode == 0, signed.stderr.decode(errors="replace")
    assert signed.stdout.startswith(b"-----BEGIN PGP SIGNATURE-----")

    sig_path = stack.workdir / "nonce.sig"
    sig_path.write_bytes(signed.stdout)
    data_path = stack.workdir / "nonce.txt"
    data_path.write_bytes(nonce)
    verified = stack.gpg("--status-fd", "1", "--verify", str(sig_path), str(data_path))
    assert verified.returncode == 0, verified.stderr.decode(errors="replace")
    assert f"VALIDSIG {stack.gpg_fingerprint}" in verified.stdout.decode()

    # The throwaway home is the *only* place this key lives: the operator's
    # default keyring must not have been touched.
    if shutil.which("gpg"):
        default = subprocess.run(
            ["gpg", "--list-keys", "--with-colons", stack.gpg_fingerprint],
            capture_output=True, text=True, timeout=60, check=False,
            env={k: v for k, v in os.environ.items() if k != "GNUPGHOME"},
        )
        assert stack.gpg_fingerprint not in default.stdout


def test_chat_server_process_is_alive_and_serving(stack):
    """The real ``chat_server.py`` is running and answering on its own port."""
    assert stack.chat.is_running(), stack.chat.output()
    os.kill(stack.chat.pid, 0)  # raises if the pid is gone

    with socket.create_connection(("127.0.0.1", stack.chat_port), timeout=10):
        pass

    status, headers, body = _stack.http_get(stack.chat_port, "/api/agents")
    assert status == 200
    assert headers["content-type"].startswith("application/json")
    # Answering this at all means the server opened the SQLite file the stack
    # gave it and queried a real schema.
    assert isinstance(json.loads(body)["agents"], list)

    status, headers, _ = _stack.http_get(stack.chat_port, "/sse")
    assert status == 200
    assert headers["content-type"].startswith("text/event-stream")


def test_poller_process_is_alive_and_logged_into_the_test_mailbox(stack):
    """The real ``main.py`` is running and has authenticated over verified TLS.

    ``src/poller.py`` emits this line only after ``IMAP4_SSL`` completed a
    verified handshake *and* ``login()`` returned OK, so it is evidence of a
    working session rather than of a process that started and is retrying.
    """
    assert stack.poller.is_running(), stack.poller.output()
    os.kill(stack.poller.pid, 0)

    line = stack.poller.wait_for_output(r"IMAP connected to \S+ as \S+")
    assert f"127.0.0.1:{stack.imaps_port}" in line
    assert line.endswith(stack.polled_account.login)
    assert "Claude Email Agent starting" in stack.poller.output()


def test_child_processes_cannot_reach_the_operators_mailbox(stack):
    """The kernel's view of both children contains only stack variables.

    Scope, precisely: ``/proc/<pid>/environ`` is the environment as of ``exec``
    and does not change when a process later mutates its own environment. So
    this test pins the variables the harness *hands over* — the channel that
    would carry the operator's live IMAP credentials into a poller — and
    nothing more. Configuration the children load for themselves afterwards is
    a different channel, pinned by
    ``test_children_load_config_from_the_harness_run_root_not_the_checkout``.

    The oracle is ``EXPECTED_CHILD_KEYS`` above — a literal written in this
    file — and ``os.environ`` of the pytest process, which *is* the operator's
    environment. Neither comes from ``_stack.build_stack_env``, so a regression
    that widened that function (say, ``return {**os.environ, **env}``) cannot
    widen the thing judging it. Comparing against ``stack.env`` alone would be
    a tautology for exactly that mutation.

    An inherited-credentials poller would consume the operator's live mailbox,
    which is why this channel is checked against oracles the harness does not
    produce.
    """
    # Guard against a vacuous probe: if the whitelist happened to cover the
    # operator's whole environment there would be nothing left to leak, and
    # every assertion below would pass without meaning anything.
    operator_only = set(os.environ) - EXPECTED_CHILD_KEYS
    assert operator_only, "probe is vacuous — the operator env has no distinctive keys"

    leak_prone = ("IMAP_HOST", "IMAP_PORT", "SMTP_HOST", "EMAIL_ADDRESS",
                  "EMAIL_PASSWORD", "AUTHORIZED_SENDER", "SHARED_SECRET",
                  "CHAT_DB_PATH", "STATE_FILE", "GNUPGHOME")
    for child in (stack.chat, stack.poller):
        seen = child.environ()

        # 1. Independent oracle: exactly the keys this file says, no more and
        #    no fewer. Equality also catches a variable silently dropped.
        assert set(seen) == EXPECTED_CHILD_KEYS, (
            f"{child.name} env diverged — extra: {set(seen) - EXPECTED_CHILD_KEYS}, "
            f"missing: {EXPECTED_CHILD_KEYS - set(seen)}")

        # 2. Positive leak probe against the live operator environment.
        assert not (set(seen) & operator_only), (
            f"{child.name} inherited operator variables: {set(seen) & operator_only}")

        # 3. The values really are the stack's — this is what catches a leak
        #    introduced at the spawn site rather than at the construction site.
        for key in leak_prone:
            assert seen.get(key) == stack.env[key], f"{child.name} {key} diverged"
        assert seen["IMAP_HOST"] == "127.0.0.1"
        assert seen["EMAIL_ADDRESS"].endswith(stack.polled_account.login)
        assert not seen["EMAIL_ADDRESS"].endswith(".dk")
        assert seen["CLAUDE_BIN"].startswith(str(stack.workdir))


def test_children_load_config_from_the_harness_run_root_not_the_checkout(stack, tmp_path):
    """Post-exec config loading is anchored in a directory the harness owns.

    ``/proc/<pid>/environ`` is the *exec-time* snapshot, so the test above
    cannot see a child that reaches out for configuration after it starts —
    and both entry points do exactly that: ``load_dotenv()`` walks up from the
    running module's directory, and ``src/config.py`` reads ``.env.test`` from
    the same anchor. If that anchor is the operator's checkout, their ``.env``
    and ``.env.test`` are folded into the child, and ``build_universe_resources``
    eagerly creates a ChatDB at whatever ``CHAT_DB_PATH`` their ``.env.test``
    names — a write outside the harness, on the operator's machine.

    The oracle is a *positive* one, and deliberately so: planting a file in the
    checkout to prove it is ignored would itself clobber operator state. So a
    ``.env.test`` naming a harness-owned ``CHAT_DB_PATH`` is planted in the
    probe's run-root, and the real ``main.py`` is booted from there. Config
    resolution has exactly one anchor; if the created database proves the
    anchor is this throwaway directory, then it is not the checkout.

    The probe's IMAP port is a closed one, so it never reaches a mailbox:
    ``main.py`` builds its universes (and therefore its databases) before it
    ever calls ``poller.connect()``.
    """
    runroot = _stack.stage_run_root(tmp_path / "probe-root")
    probe_db = tmp_path / "planted-universe.db"
    (runroot / ".env.test").write_text(
        f"SENDER=e2e-probe@{stack.mailserver.domain}\n"
        f"CHAT_DB_PATH={probe_db}\n",
    )

    env = dict(stack.env)
    env["IMAP_PORT"] = str(_stack.free_port())  # nothing is listening there
    env["CHAT_DB_PATH"] = str(tmp_path / "probe-primary.db")
    env["STATE_FILE"] = str(tmp_path / "probe_ids.json")
    env["LOG_FILE"] = str(tmp_path / "probe.log")

    child = _stack.spawn("probe_poller", "main.py", env, tmp_path, runroot=runroot)
    try:
        child.wait_for_path(probe_db, timeout=30.0)
    finally:
        child.stop()

    assert probe_db.exists(), child.output()
    # And the stack's own run-root carries no test universe at all: only the
    # empty .env that stops the upward walk, never a .env.test.
    assert (stack.runroot / ".env").read_text() == ""
    assert not (stack.runroot / ".env.test").exists()
