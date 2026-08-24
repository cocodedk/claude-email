# End-to-end testing

The unit suite proves that each module behaves correctly *given* a mail server.
The e2e suite proves the mail actually moves. It uses no mocks at all: a real
mail server in a container, real sockets, real SMTP and IMAP protocol
exchanges.

## What is here

```
tests/e2e/
├── docker-compose.yml               # the mail server — the whole configuration
├── conftest.py                      # session fixture + the `e2e` marker
└── test_mailserver_roundtrip.py     # SMTP -> IMAP round trip
```

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

The TLS listeners are published but unused so far. `src/mailer.py` and
`src/poller.py` speak implicit TLS only (`SMTP_SSL` / `IMAP4_SSL` with a
verified context), so later slices that drive the production code will need
them.

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
