# Claude Email Agent — Implementation Plan

## Overview

A Python service that polls `claude@cocode.dk` via IMAP, filters for commands
sent exclusively by `bb@cocode.dk`, executes them via the `claude` CLI, and
replies with the output. Runs as a systemd service.

## Connection Details

| Protocol | Host           | Port |
|----------|----------------|------|
| IMAP     | imap.one.com   | 993  |
| SMTP     | send.one.com   | 465  |

Credentials stored in `.env`, loaded via `python-dotenv`.

---

## Security Model

**Threat**: Email `From:` headers are trivially spoofable. This system executes
arbitrary shell commands, so sender validation is load-bearing.

**Mitigation layers (implemented)**:
1. Check `From:` header contains `bb@cocode.dk`
2. Check `Return-Path` header matches `bb@cocode.dk`
3. Reject if either header is missing or mismatched

**Recommended hardening (post-MVP)**:
- DKIM signature verification via `dkimpy` library
- SPF record check

---

## File Structure

```
claude-email/
├── src/
│   ├── __init__.py
│   ├── poller.py      # IMAP: connect, fetch unseen, mark seen
│   ├── security.py    # Sender validation (From + Return-Path)
│   ├── executor.py    # Parse command from body, run claude CLI, capture output
│   └── mailer.py      # SMTP: send reply
├── tests/
│   ├── __init__.py
│   ├── test_poller.py
│   ├── test_security.py
│   ├── test_executor.py
│   └── test_mailer.py
├── main.py            # Main loop: signal handling, poll interval, service entry
├── .env.example
├── .gitignore
├── pyproject.toml
├── requirements.txt
└── claude-email.service   # systemd unit file
```

---

## Module Responsibilities

### `src/security.py`

```python
def is_authorized(message: email.message.Message) -> bool
```
- Extract `From` and `Return-Path` headers
- Both must match `bb@cocode.dk`
- Return `False` (and log a warning) if any check fails

### `src/executor.py`

```python
def extract_command(message: email.message.Message) -> str
def execute_command(command: str, timeout: int = 300) -> str
```
- `extract_command`: reads plain-text body, strips quoted replies
- `execute_command`: runs `claude --print "<command>"` via `subprocess.run`
  with timeout, captures stdout+stderr
- On timeout: returns error message, does not crash

### `src/poller.py`

```python
class EmailPoller:
    def connect(self) -> None
    def fetch_unseen(self) -> list[email.message.Message]
    def mark_seen(self, uid: str) -> None
    def disconnect(self) -> None
```
- Uses `imaplib.IMAP4_SSL` (port 993)
- Fetches UNSEEN emails only
- Marks each email as `\Seen` immediately after fetching to prevent replay

### `src/mailer.py`

```python
def send_reply(to: str, subject: str, body: str) -> None
```
- Uses `smtplib.SMTP_SSL` (port 465)
- Connects fresh per send (avoids stale connection issues in long-running service)
- Subject: `Re: {original_subject}`

### `main.py`

```python
def run_loop(poll_interval: int = 30) -> None
```
- Polls every `poll_interval` seconds (configurable via `POLL_INTERVAL` env var)
- Signal handlers for `SIGTERM`/`SIGINT` → graceful shutdown
- Structured logging via Python `logging` module (journald-compatible)
- Flow per iteration:
  1. `poller.fetch_unseen()`
  2. For each email: `security.is_authorized()`
  3. If authorized: `executor.extract_command()` → `executor.execute_command()`
  4. `mailer.send_reply()`
  5. `poller.mark_seen(uid)`

---

## TDD Order

Tests are written first, implementation follows:

1. **`test_security.py`** — sender validation (allowed, rejected, spoofed, missing headers)
2. **`test_executor.py`** — command extraction, subprocess mock for claude CLI, timeout
3. **`test_poller.py`** — IMAP connection mock, fetch unseen, mark seen
4. **`test_mailer.py`** — SMTP mock, reply construction
5. **Integration** in `test_main.py` (optional, uses all mocks together)

---

## Dependencies

```
python-dotenv    # .env loading
pytest           # test runner
pytest-mock      # mocker fixture
```

No external IMAP/SMTP libraries — use Python stdlib `imaplib`/`smtplib`.

---

## Environment Variables (.env)

```
IMAP_HOST=imap.one.com
IMAP_PORT=993
SMTP_HOST=send.one.com
SMTP_PORT=465
EMAIL_ADDRESS=claude@cocode.dk
EMAIL_PASSWORD=<password>
AUTHORIZED_SENDER=bb@cocode.dk
POLL_INTERVAL=30
CLAUDE_TIMEOUT=300
```

---

## Systemd Service (claude-email.service)

```ini
[Unit]
Description=Claude Email Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=__USER__
WorkingDirectory=__INSTALL_DIR__
EnvironmentFile=__INSTALL_DIR__/.env
ExecStart=/usr/bin/python3 __INSTALL_DIR__/main.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

---

## Out of Scope (MVP)

- DKIM verification (add post-MVP)
- Rate limiting per sender
- Command allow-listing
- Web dashboard
- Mobile app integration (future phase)
