# End-to-end testing

The unit suite proves that each module behaves correctly *given* a mail server.
The e2e suite proves the mail actually moves. It uses no mocks at all: a real
mail server in a container, real sockets, real SMTP and IMAP protocol
exchanges.

## What is here

```
tests/e2e/
├── docker-compose.yml               # the mail server — the whole configuration
├── conftest.py                      # session fixtures + the `e2e` marker
├── _stack.py                        # boots the real stack: TLS, GPG, bus, poller
├── test_mailserver_roundtrip.py     # SMTP -> IMAP round trip
├── test_stack_boots.py              # the whole system is genuinely running
├── test_happy_path.py               # one real command in, real reply out
├── test_auth_matrix.py              # every route x every auth condition
├── test_replay.py                   # a captured real message, replayed twice
├── test_metamorphic_headers.py      # one payload, mutated unsigned headers
└── test_invariants.py               # properties over a whole stream of real mail
```

`test_auth_matrix.py` is the authentication grid — six inbound routes against
five conditions (unsigned, wrong key, stale timestamp, replayed nonce, valid),
every cell asserted over real mail. It also boots a second poller with
`SHARED_SECRET=""` to cover the GPG-only deployment. See
[the authentication matrix](e2e-auth-matrix.md) for the grid, the
no-freshness-window finding and the operator hand-off it carries.

`test_replay.py` captures one real signed command off the wire and hands it
back twice — byte-identical, then under a fresh `Message-ID` — and counts the
effect rather than the rejection. See [replaying a captured
message](e2e-replay.md).

`test_metamorphic_headers.py` re-sends one captured signed payload under
mutated `Subject` / `In-Reply-To` / `References` / `To` and asserts the
executed command, the routing target and the reconstructed prompt never move
— see [mutating the unsigned envelope](e2e-metamorphic-headers.md).

`test_invariants.py` runs a generated stream of real messages through a second
live poller in shared-secret mode and asserts three properties over the whole
batch: the secret appears in no outbound body **and no outbound header**, every
accepted inbound message has exactly one ledger row, and no effect is observed
twice. The header half found a live leak — replies thread on the inbound
Subject, and `AUTH:<secret> <command>` is a supported Subject. See
[invariants over real traffic](e2e-invariants.md), which also carries the
secret-rotation hand-off.

## Running it

```bash
.venv/bin/pytest tests/                 # everything; e2e skips without docker
.venv/bin/pytest tests/ -m "not e2e"    # unit tests only — no docker needed
.venv/bin/pytest tests/ -m e2e          # e2e only
```

Every test under `tests/e2e/` is marked `e2e` automatically by
`pytest_collection_modifyitems`, so nothing has to remember to add the marker.

## Requirements, and what happens without them

The suite needs `docker` on `PATH`, a working `docker compose`, and a reachable
daemon. If any of those is missing the whole directory **skips** — it never
fails — and the skip message says which one:

```
SKIPPED — e2e mail server needs docker — docker executable not found on PATH
SKIPPED — e2e mail server needs docker — docker daemon not reachable: ...
```

Docker is therefore an opt-in CI prerequisite, not a developer prerequisite.

## The mail server

[GreenMail](https://greenmail-mail-test.github.io/greenmail/) standalone. It is
a complete SMTP/IMAP/POP3 server, not a stub; the tests speak to it with
`smtplib` and `imaplib` over ordinary TCP.

It is used in preference to Postfix + Dovecot because `docker-compose.yml` is
the entire configuration — no Dockerfile, no mounted config tree — so the
harness reproduces on any machine that has nothing but docker.

### Ports

Published on `127.0.0.1` only, and never on a default mail port, so a running
suite can neither reach nor be reached by the operator's real mailbox.

| Protocol | Host port | Container port | Override |
|----------|-----------|----------------|----------|
| SMTP     | 13025     | 3025           | `CLAUDE_EMAIL_E2E_SMTP_PORT` |
| IMAP     | 13143     | 3143           | `CLAUDE_EMAIL_E2E_IMAP_PORT` |
| SMTPS    | 13465     | 3465           | `CLAUDE_EMAIL_E2E_SMTPS_PORT` |
| IMAPS    | 13993     | 3993           | `CLAUDE_EMAIL_E2E_IMAPS_PORT` |

`src/mailer.py` and `src/poller.py` speak implicit TLS only (`SMTP_SSL` /
`IMAP4_SSL` with a verified context). GreenMail's own TLS listeners cannot
serve them — see *The TLS terminator* below — so the `stack` fixture puts its
own terminator in front of the plaintext ports instead.

### Accounts

Fake domain `e2e.test`, defined by `-Dgreenmail.users` in the compose file and
mirrored in `conftest.ACCOUNTS`:

| Role      | Address                    | Password       |
|-----------|----------------------------|----------------|
| sender    | `e2e-sender@e2e.test`      | `sender-pw`    |
| recipient | `e2e-recipient@e2e.test`   | `recipient-pw` |
| bystander | `e2e-bystander@e2e.test`   | `bystander-pw` |

These are throwaway credentials for a container listening on loopback. No real
address ever appears in the tests.

## Gotchas worth knowing before you extend this

**Log in with the local part, not the address.** GreenMail authenticates
`e2e-sender`, and rejects `e2e-sender@e2e.test` as a bad credential. `Account`
keeps `login` and `address` separate for exactly this reason.

**A connectable port does not mean a ready server.** Docker publishes ports
through a userland proxy that accepts connections from the moment the container
is created — before the JVM inside has bound anything. Such a connection is
accepted and instantly closed, which looks like readiness and then fails the
first command. The fixture therefore waits for the actual SMTP `220` and IMAP
`* OK` greetings.

**A body has no trailing CRLF once delivered.** In SMTP the body's final line
terminator is part of the `\r\n.\r\n` DATA terminator and is consumed by it, so
what the server stores ends at the last character of the last line. The
expected value in the test is written that way. Do not "fix" a mismatch by
stripping bytes off the retrieved message — that converts byte-identity into
approximate identity and defeats the point of the test.

**GreenMail auto-creates unknown local recipients.** It will not refuse mail to
an address that has no account, so it cannot be used to test recipient
validation.

**Delivery is asynchronous.** Use `mailserver.wait_for(...)`, which polls until
the message lands and fails only on timeout.

## The running stack

`test_mailserver_roundtrip.py` proves the transport. The `stack` fixture goes
further: it boots the actual product — `chat_server.py` and `main.py` — as
operating-system processes against that transport, and `test_stack_boots.py`
asserts each part is genuinely alive.

```python
def test_something(stack):
    stack.chat.is_running()          # real chat_server.py process
    stack.poller.is_running()        # real main.py poller process
    stack.imaps_port, stack.smtps_port   # verified-TLS endpoints
    stack.gnupghome, stack.gpg_fingerprint   # throwaway keyring, real key
```

What it starts, in order, and tears down in reverse:

| Component      | How it is proved alive                                        |
|----------------|---------------------------------------------------------------|
| mail server    | SMTP `220` / IMAP `* OK` greetings (the `mailserver` fixture)  |
| GPG keyring    | `gpg --detach-sign` of a nonce that `gpg --verify` accepts     |
| chat bus       | `GET /api/agents` → 200 JSON, `GET /sse` → `text/event-stream` |
| poller         | `IMAP connected to …` in its output — printed only after a verified handshake **and** a successful `login()` |

### The TLS terminator

GreenMail's built-in TLS presents a self-signed certificate whose subject is
`CN=GreenMail selfsigned Test Certificate` with **no subjectAltName**. No client
that verifies hostnames can accept that, whatever CA it trusts — and turning
verification off in `src/poller.py` would break a repo invariant.

So the harness runs the equivalent of `stunnel`: a protocol-blind byte pump
that terminates TLS with a locally generated certificate (SAN `127.0.0.1`) and
forwards plaintext to GreenMail's plaintext port. It parses nothing; every byte
of SMTP and IMAP is still spoken by the real server. The child processes get
`SSL_CERT_FILE` pointing at that certificate, so
`ssl.create_default_context()` trusts it — and
`test_mail_server_answers_verified_tls_on_both_transports` asserts the negative
control: without that CA the same endpoint is rejected with
`SSLCertVerificationError`.

### The child environment

The children are handed an environment **built from scratch**, never a copy of
`os.environ`. The keys mirror `.env.example`, so the real
`build_config` / `build_universes` seam runs unmodified inside the real
`main.py`.

That closes the *exec-time* channel only. Both entry points then fetch more
configuration on their own: `load_dotenv()` walks up from the running module's
directory looking for a `.env`, and `src/config.py` reads `.env.test` from the
directory two levels above its own `__file__`. Anchored at a real checkout,
that folds the operator's `.env` into the child, and `build_universe_resources`
eagerly creates a database at whatever `CHAT_DB_PATH` their `.env.test` names.

### The staged run-root

So the children are started from a throwaway **run-root** instead of the
checkout. `stage_run_root()` symlinks the packages (`src/`, `chat/`, `scripts/`)
and copies the two entry-point files into it, then plants an empty `.env`:

- `os.path.abspath()` — what both anchors use — leaves symlinks unresolved, so
  `src/config.py` sees a `__file__` inside the run-root while executing the
  repo's own bytes.
- CPython *does* resolve the script path when computing `sys.path[0]`, so
  launching a symlinked `main.py` would put the checkout back on `sys.path`.
  Copying `main.py` and `chat_server.py` (fresh on every boot) avoids that.
- The empty `.env` terminates `find_dotenv()`'s upward walk.

A guard that *failed* when a `.env` was present was rejected: the operator's
checkout always has one, and that would make the e2e suite unrunnable exactly
where it matters. `test_children_load_config_from_the_harness_run_root_not_the_checkout`
pins the guarantee by planting a `.env.test` in a probe run-root and proving the
database it names — and only that one — is what the real `main.py` creates.

`test_child_processes_cannot_reach_the_operators_mailbox` reads
`/proc/<pid>/environ` and asserts the children hold only stack credentials.
That absence is the one that matters: a poller that inherited real IMAP
credentials would start consuming the operator's live mailbox.

`CLAUDE_BIN` points at a stub that exits non-zero on stderr. This slice boots
the stack; it does not run commands, and a stub that failed silently would let
an accidental CLI invocation pass unnoticed.

### Teardown

Unconditional, in reverse dependency order, even if the boot itself raised:
poller first (it is the component that talks to a mailbox), then the chat
server, then the terminators, then the `gpg-agent` holding the throwaway
keyring open. Both children run in their own process group and are stopped with
`SIGTERM` to the group, escalating to `SIGKILL` — the chat server supervises
workers and a wake watcher, and those must not outlive it.


## One real command, end to end

`test_happy_path.py` is the first test that drives the product rather than the
harness. A GPG-signed `multipart/signed` mail is handed to the real SMTP server
by a real client; the real `main.py` polls it over real IMAP, verifies the
signature with the real `gpg` binary, routes it, forks the configured CLI, and
mails the output back. Everything asserted is read from **outside** the poller
process — the reply mail pulled back over IMAP, the file the executed process
left on disk, and the rows in the real SQLite bus. No `Child` handle, no log
scraping, no patching.

### The designed oracle

The expected effect is computed *before* the mail is sent: the exact prompt
string the CLI must receive, its byte length, and its SHA-256, all derived from
the bytes put on the wire using the standard library alone. Nothing is asserted
against a value the system itself produced. The command carries CRLF and two
non-ASCII scripts, so any normalisation between MIME and `execve` shows up as a
mismatch.

Two mutations were run to confirm the test bites: signing the wrong bytes (the
mail is dropped as unauthorised — no reply ever arrives, all four tests fail),
and appending one byte to the body in flight while leaving the prediction alone
(the digest, the receipt and the bus row all diverge).

### Signing what the verifier will actually check

`src/gpg_verify.py` verifies `part.as_bytes()` of the *parsed* message, and the
stdlib parser normalises the part's line endings on reserialisation. The test
does not guess at that transformation: it parses a copy of the very message it
is about to send and signs whatever comes out. The signature part cannot affect
the first part's serialisation, so a placeholder is enough.

### Why the CLI is a stand-in, and why that is not a mock

The `claude` CLI is outside the system under test — it is the third-party
program claude-email shells out to, and it is non-deterministic, costs money,
and needs network. So this test replaces the harness's refusing `CLAUDE_BIN`
stub with a deterministic real executable that reports a pure function of its
prompt and writes a receipt file, then restores the original bytes on teardown.
It is reached by a real `fork`/`exec` from the real `src/executor.py`, with a
real argv, and its real stdout travels back through the real mailer. Every
component in scope — IMAP, GPG, routing, subprocess, SMTP, SQLite — runs
unmodified.
