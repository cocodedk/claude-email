# CLAUDE.md — claude-email

## Project Overview

Email-driven wrapper for the Claude Code CLI. Polls `claude@cocode.dk` via IMAP, verifies that commands come exclusively from `bb@cocode.dk` (GPG signature or shared secret), executes them via `claude --print`, and replies via SMTP. Runs as a hardened systemd service.

- **Language / Runtime**: Python 3.12
- **Architecture**: Single-process service with 4 source modules + main loop
- **Test runner**: pytest (34 tests)

---

## Required Skills — ALWAYS Invoke These

| Situation | Skill |
|-----------|-------|
| Before any new feature | `superpowers:brainstorming` |
| Planning multi-step changes | `superpowers:writing-plans` |
| Writing or fixing any logic | `superpowers:test-driven-development` |
| First sign of a bug or failure | `superpowers:systematic-debugging` |
| Before completing a feature branch | `superpowers:requesting-code-review` |
| Before claiming any task done | `superpowers:verification-before-completion` |
| After implementing — reviewing quality | `simplify` |

---

## Architecture

```
claude-email/
├── src/
│   ├── security.py    # Sender validation: From, Return-Path, GPG or shared secret
│   ├── executor.py    # Extract command from body, run claude CLI (shell=False)
│   ├── poller.py      # IMAP4_SSL polling, Message-ID idempotency store
│   └── mailer.py      # SMTP_SSL reply with threading headers
├── tests/             # 34 pytest tests — one per behaviour
├── main.py            # Poll loop, signal handling, config from .env
├── install.sh         # Installer: venv + systemd
└── claude-email.service
```

### Key invariants
- `security.py` never imports from `executor.py`, `poller.py`, or `mailer.py`
- All subprocess calls use `shell=False`
- All TLS connections use `ssl.create_default_context()` (verified, not default unverified)
- `processed_ids.json` is the idempotency store — never delete it in production

### Systemd
- Runs as a **user-level** service (`~/.config/systemd/user/claude-email.service`)
- Can restart itself: `systemctl --user restart claude-email.service`
- No sudo required — user-level systemd with lingering enabled

---

## Engineering Principles

- **200-line maximum per file** — extract when approaching limit
- **TDD**: write failing test first, then minimal implementation
- **No shell=True** in subprocess calls — command injection risk
- **No secrets in logs** — never log passwords, secrets, or raw command output

---

## Build Commands

```bash
.venv/bin/pytest tests/ -q      # Run all 34 tests
.venv/bin/pytest tests/ -v      # Verbose
```

---

## Starting a New Session

1. Read this file
2. Run `.venv/bin/pytest tests/ -q` — confirm 34 tests pass
3. Invoke `superpowers:brainstorming` before any feature work
