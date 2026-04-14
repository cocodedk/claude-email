# claude-email

An email-driven wrapper for [Claude Code CLI](https://claude.ai/code). Polls `agent@example.com` for commands from `user@example.com`, executes them via `claude --print`, and replies with the output. Runs as a systemd service.

## Features

- Polls IMAP mailbox every 30 seconds (configurable)
- Dual-layer sender verification: GPG signature **or** shared secret in Subject
- Multi-header envelope check (From + Return-Path exact match)
- Executes commands via `claude --print` with configurable timeout
- Replies via SMTP with email threading headers (In-Reply-To, References)
- Idempotent — tracks processed Message-IDs to prevent replay
- Runs as a hardened systemd service (NoNewPrivileges, ProtectSystem)

## Requirements

- Python 3.11+
- [Claude Code CLI](https://claude.ai/code) installed and authenticated
- GPG key for `user@example.com` (recommended) or a shared secret

## Install

```bash
git clone https://github.com/cocodedk/claude-email.git
cd claude-email
cp .env.example .env
# Edit .env — set EMAIL_PASSWORD, SHARED_SECRET or GPG_FINGERPRINT
./install.sh
```

## Configuration (.env)

| Variable | Description |
|---|---|
| `EMAIL_ADDRESS` | IMAP/SMTP account (`agent@example.com`) |
| `EMAIL_PASSWORD` | Account password |
| `AUTHORIZED_SENDER` | Only process emails from this address (`user@example.com`) |
| `SHARED_SECRET` | Subject prefix secret (`AUTH:<secret> command`) |
| `GPG_FINGERPRINT` | GPG key fingerprint — enables GPG mode (recommended) |
| `POLL_INTERVAL` | Seconds between polls (default: 30) |
| `CLAUDE_TIMEOUT` | Max seconds for claude CLI execution (default: 300) |

## Sending a Command

**GPG mode** (recommended): compose a GPG-signed email to `agent@example.com`. Subject can be anything.

**Shared secret mode**: set Subject to `AUTH:your_secret your command here`. Email body contains the command.

## Architecture

```
claude-email/
├── src/
│   ├── security.py    # Sender validation (From, Return-Path, GPG/secret)
│   ├── executor.py    # Command extraction + claude CLI runner
│   ├── poller.py      # IMAP polling + Message-ID idempotency
│   └── mailer.py      # SMTP reply sender
├── tests/             # 34 tests (pytest)
├── main.py            # Poll loop + signal handling
├── install.sh         # One-command installer
└── claude-email.service  # systemd unit
```

## Author

**Babak Bandpey** — [cocode.dk](https://cocode.dk) | [LinkedIn](https://linkedin.com/in/babakbandpey) | [GitHub](https://github.com/cocodedk)

## License

Apache-2.0 | © 2026 [Cocode](https://cocode.dk) | Created by [Babak Bandpey](https://linkedin.com/in/babakbandpey)
