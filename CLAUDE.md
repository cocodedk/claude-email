# CLAUDE.md — claude-email

## Project Overview

Email-driven wrapper for the Claude Code CLI with an integrated chat relay for managing multiple Claude Code agents. Polls `claude@cocode.dk` via IMAP, verifies that commands come exclusively from `bb@cocode.dk` (GPG signature or shared secret), executes them via `claude --print`, and replies via SMTP. Includes an MCP-based chat system where claude-email acts as the user's avatar, brokering conversations between the user (via email) and multiple Claude Code agents (via MCP tools).

- **Language / Runtime**: Python 3.12
- **Architecture**: Two user-level systemd services — claude-email (poller + user avatar) and claude-chat (MCP SSE server + SQLite message bus)
- **Test runner**: pytest (143 tests)

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
│   ├── security.py        # Sender validation: From, Return-Path, GPG or shared secret
│   ├── executor.py        # Extract command from body, run claude CLI (shell=False)
│   ├── poller.py          # IMAP4_SSL polling, Message-ID idempotency store
│   ├── mailer.py          # SMTP_SSL reply with threading headers + Message-ID generation
│   ├── chat_db.py         # Shared SQLite layer (WAL mode) — agents, messages, events
│   ├── chat_router.py     # Email→chat routing: reply, @agent, meta, CLI fallback
│   ├── chat_handlers.py   # Chat dispatch + relay outbound agent→user emails
│   └── spawner.py         # Spawn Claude Code agents, inject MCP config
├── chat/
│   ├── tools.py           # MCP tool implementations (register, ask, notify, check, list, deregister)
│   └── server.py          # MCP SSE server (Starlette + low-level mcp.server)
├── tests/                 # 131 pytest tests
├── main.py                # Poll loop, signal handling, config from .env, chat integration
├── chat_server.py         # Systemd entry point for claude-chat service
├── install.sh             # Installer: venv + both systemd services
├── claude-email.service   # User-level systemd unit
└── claude-chat.service    # User-level systemd unit (MCP SSE server)
```

### Key invariants
- `security.py` never imports from `executor.py`, `poller.py`, or `mailer.py`
- All subprocess calls use `shell=False`
- All TLS connections use `ssl.create_default_context()` (verified, not default unverified)
- `processed_ids.json` is the idempotency store — never delete it in production
- `claude-chat.db` is the shared SQLite database (WAL mode) — used by both services

### Chat system
- **claude-email** is the user's avatar on the chat bus — routes emails to agents, relays agent messages back as emails
- **claude-chat** is a pure MCP message bus (SSE transport, SQLite storage)
- Email commands: `@agent-name <instruction>` to message agents, `status` for agent list, `spawn <path>` to start agents
- Reply threading: In-Reply-To header matched against DB-stored email_message_id
- Agents use MCP tools: `chat_register`, `chat_ask` (blocking), `chat_notify`, `chat_check_messages`, `chat_list_agents`, `chat_deregister`

### Systemd
- Both run as **user-level** services (`~/.config/systemd/user/`)
- claude-chat starts first (claude-email depends on it via `After=`)
- claude-email can restart itself: `systemctl --user restart claude-email.service`
- claude-email can restart claude-chat: `systemctl --user restart claude-chat.service`
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
.venv/bin/pytest tests/ -q      # Run all 143 tests
.venv/bin/pytest tests/ -v      # Verbose
```

---

## Starting a New Session

1. Read this file
2. Run `.venv/bin/pytest tests/ -q` — confirm 143 tests pass
3. Invoke `superpowers:brainstorming` before any feature work
