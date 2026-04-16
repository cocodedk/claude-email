# claude-email

An email-driven wrapper for the [Claude Code CLI](https://claude.ai/code) with an integrated chat relay for managing multiple Claude Code agents. Polls an IMAP mailbox for commands, executes them via `claude --print`, and replies via SMTP. Includes a full MCP-based chat system where `claude-email` acts as the user's avatar, brokering conversations between the user (via email) and multiple Claude Code agents (via MCP tools).

## How It Works

```
                         ┌──────────────┐
                         │  User Email  │
                         │ bb@cocode.dk │
                         └──────┬───────┘
                                │ IMAP / SMTP
                                ▼
┌───────────────────────────────────────────────────────────┐
│                     claude-email                          │
│           (poller + CLI executor + user avatar)           │
│                                                           │
│  ┌─────────┐  ┌──────────┐  ┌───────────┐  ┌──────────┐ │
│  │ poller  │  │ security │  │ executor  │  │  mailer  │ │
│  │ (IMAP)  │  │ (GPG/    │  │ (claude   │  │ (SMTP)   │ │
│  │         │  │  secret)  │  │  --print) │  │          │ │
│  └─────────┘  └──────────┘  └───────────┘  └──────────┘ │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐ │
│  │ chat_router │  │chat_handlers │  │    spawner       │ │
│  │ (classify   │  │ (dispatch +  │  │ (spawn agents,   │ │
│  │  emails)    │  │  relay)      │  │  inject MCP)     │ │
│  └─────────────┘  └──────────────┘  └──────────────────┘ │
└───────────────────────────┬───────────────────────────────┘
                            │ SQLite (WAL)
                            ▼
                  ┌───────────────────┐
                  │   claude-chat.db  │
                  │  (shared state)   │
                  └─────────┬─────────┘
                            │ SQLite (WAL)
                            ▼
┌───────────────────────────────────────────────────────────┐
│                     claude-chat                           │
│            (MCP SSE server + message bus)                 │
│                                                           │
│  MCP Tools: register, ask, notify, check, list, deregister│
└──────────┬────────────────┬───────────────┬───────────────┘
           │ MCP/SSE        │ MCP/SSE       │ MCP/SSE
           ▼                ▼               ▼
    ┌────────────┐   ┌────────────┐  ┌────────────┐
    │ agent-fits │   │ agent-api  │  │ agent-web  │
    │  (Claude   │   │  (Claude   │  │  (Claude   │
    │   Code)    │   │   Code)    │  │   Code)    │
    └────────────┘   └────────────┘  └────────────┘
```

## Features

### Email Agent
- Polls IMAP mailbox at a configurable interval
- Dual-layer sender verification: GPG signature **or** shared secret in Subject
- Multi-header envelope check (From + Return-Path exact match)
- Executes commands via `claude --print` with configurable timeout and working directory
- Replies via SMTP with email threading headers (In-Reply-To, References)
- Idempotent — tracks processed Message-IDs to prevent replay

### Chat Relay
- MCP SSE server acting as a message bus between user and agents
- SQLite database with WAL mode for safe concurrent access
- Six MCP tools for agent communication (register, ask, notify, check, list, deregister)
- `chat_ask` blocks until the user replies — no timeout
- Agent-to-user messages relayed as emails with proper threading
- User replies routed back to the correct agent via In-Reply-To matching

### Agent Management
- Spawn Claude Code agents in any project directory via email
- Automatic MCP config injection (`.mcp.json`) so agents discover the chat server
- Agent status tracking (running, idle, disconnected, deregistered)
- Agent PIDs recorded in the database

### Service Management
- Two user-level systemd services (no sudo required)
- Restart either service via email command
- Lingering enabled for headless operation

## Requirements

- Python 3.11+
- [Claude Code CLI](https://claude.ai/code) installed and authenticated
- GPG key for the authorized sender (recommended) or a shared secret

## Install

```bash
git clone https://github.com/cocodedk/claude-email.git
cd claude-email
cp .env.example .env
# Edit .env — fill in ALL required variables
./install.sh
```

The installer creates a Python virtual environment, installs dependencies, and enables both systemd services. `claude-chat` starts first, then `claude-email`.

## Configuration (.env)

Every config value is read from `.env` — no hardcoded defaults in code.

### Email

| Variable | Description | Example |
|---|---|---|
| `IMAP_HOST` | IMAP server hostname | `imap.one.com` |
| `IMAP_PORT` | IMAP server port | `993` |
| `SMTP_HOST` | SMTP server hostname | `send.one.com` |
| `SMTP_PORT` | SMTP server port | `465` |
| `EMAIL_ADDRESS` | IMAP/SMTP account | `claude@cocode.dk` |
| `EMAIL_PASSWORD` | Account password | |
| `AUTHORIZED_SENDER` | Only process emails from this address | `bb@cocode.dk` |
| `EMAIL_DOMAIN` | Domain for Message-ID generation | `cocode.dk` |

### Polling & CLI

| Variable | Description | Example |
|---|---|---|
| `POLL_INTERVAL` | Seconds between IMAP polls | `15` |
| `CLAUDE_TIMEOUT` | Max seconds for CLI execution | `300` |
| `CLAUDE_BIN` | Path to Claude CLI binary | `/home/user/.local/bin/claude` |
| `CLAUDE_CWD` | Working directory for CLI commands | `/home/user/projects` |
| `STATE_FILE` | Message-ID idempotency store | `processed_ids.json` |

### Chat System

| Variable | Description | Example |
|---|---|---|
| `CHAT_DB_PATH` | SQLite database file | `claude-chat.db` |
| `CHAT_HOST` | MCP server bind address | `127.0.0.1` |
| `CHAT_PORT` | MCP server port | `8420` |
| `CHAT_URL` | Full SSE endpoint URL | `http://127.0.0.1:8420/sse` |
| `SERVICE_NAME_EMAIL` | Systemd unit name for email service | `claude-email.service` |
| `SERVICE_NAME_CHAT` | Systemd unit name for chat service | `claude-chat.service` |

### Authentication

| Variable | Description | Example |
|---|---|---|
| `SHARED_SECRET` | Subject prefix secret | `change_this` |
| `GPG_FINGERPRINT` | GPG key fingerprint (enables GPG mode) | |
| `GPG_HOME` | Custom GPG home directory | |

## Sending Commands

### Direct CLI Commands

**GPG mode** (recommended): compose a GPG-signed email to the service address. Subject can be anything.

**Shared secret mode**: set Subject to `AUTH:<secret> <command>`. Email body contains the detailed instruction.

### Chat Commands

| Command | Description | Example Subject |
|---|---|---|
| `@agent-name <instruction>` | Send instruction to a specific agent | `AUTH:secret @agent-fits run the tests` |
| `status` | List all registered agents and their state | `AUTH:secret status` |
| `spawn <path> [instruction]` | Spawn a new agent in the given project | `AUTH:secret spawn /home/user/my-project` |
| `restart chat` | Restart the claude-chat service | `AUTH:secret restart chat` |
| `restart self` | Restart the claude-email service | `AUTH:secret restart self` |

### Replying to Agents

When an agent sends a message (via `chat_ask` or `chat_notify`), it arrives as an email. Reply directly to that email — the In-Reply-To header routes your reply back to the correct agent.

## Message Flow Diagrams

### Agent Asks User a Question

```
Agent                    claude-chat           claude-email            User
  │                         │                       │                   │
  │  chat_ask("question?")  │                       │                   │
  │ ───────────────────────>│                       │                   │
  │                         │  insert ask message   │                   │
  │                         │──────────────────────>│                   │
  │                         │                       │  SMTP: email      │
  │                         │                       │  with question    │
  │   (blocking...)         │                       │ ─────────────────>│
  │                         │                       │                   │
  │                         │                       │  IMAP: reply      │
  │                         │                       │<───────────────── │
  │                         │  insert reply message │                   │
  │                         │<──────────────────────│                   │
  │  { reply: "answer" }    │                       │                   │
  │<─────────────────────── │                       │                   │
```

### User Dispatches Command to Agent

```
User                  claude-email           claude-chat              Agent
  │                       │                       │                     │
  │  IMAP: "@agent do X"  │                       │                     │
  │ ─────────────────────>│                       │                     │
  │                       │  insert command msg   │                     │
  │                       │──────────────────────>│                     │
  │  SMTP: "dispatched"   │                       │                     │
  │<───────────────────── │                       │                     │
  │                       │                       │  chat_check_msgs()  │
  │                       │                       │<─────────────────── │
  │                       │                       │  { messages: [...]} │
  │                       │                       │ ───────────────────>│
  │                       │                       │                     │
  │                       │                       │  Agent executes     │
```

### Agent Sends Status Notification

```
Agent                    claude-chat           claude-email            User
  │                         │                       │                   │
  │  chat_notify("done!")   │                       │                   │
  │ ───────────────────────>│                       │                   │
  │  { status: "sent" }    │  insert notify msg    │                   │
  │<─────────────────────── │──────────────────────>│                   │
  │                         │                       │  SMTP: status     │
  │  (returns immediately)  │                       │  email            │
  │                         │                       │ ─────────────────>│
```

## Email Routing Priority

When claude-email receives an authorized email, it classifies it in this order:

```
Incoming Email
      │
      ▼
┌─────────────────────────────────┐
│ In-Reply-To matches a known     │──── yes ──> Route reply to agent
│ email_message_id in DB?         │
└─────────────┬───────────────────┘
              │ no
              ▼
┌─────────────────────────────────┐
│ Subject starts with @agent-name?│──── yes ──> Dispatch command to agent
└─────────────┬───────────────────┘
              │ no
              ▼
┌─────────────────────────────────┐
│ Subject is a meta-command?      │──── yes ──> Handle internally
│ (status, spawn, restart)        │             (query DB, spawn, systemctl)
└─────────────┬───────────────────┘
              │ no
              ▼
┌─────────────────────────────────┐
│ CLI fallback                    │──── Execute via claude --print
│ (original behavior)             │
└─────────────────────────────────┘
```

## MCP Tools (for Agents)

Agents connect to the chat server via MCP SSE and use these tools:

| Tool | Description | Blocking |
|---|---|---|
| `chat_register` | Register as a participant (name + project path) | No |
| `chat_ask` | Send a question to the user and wait for reply | Yes |
| `chat_notify` | Send a fire-and-forget status update | No |
| `chat_check_messages` | Poll for pending inbound messages | No |
| `chat_list_agents` | List all registered agents and their status | No |
| `chat_deregister` | Leave the chat system | No |

## Data Model

SQLite with WAL mode, shared by both services.

### agents

| Column | Type | Description |
|---|---|---|
| `name` | TEXT PK | e.g., `agent-fits` |
| `project_path` | TEXT | Absolute path to project directory |
| `status` | TEXT | `running`, `idle`, `disconnected`, `deregistered` |
| `pid` | INTEGER | OS process ID (if spawned) |
| `registered_at` | TEXT | ISO 8601 timestamp |
| `last_seen_at` | TEXT | ISO 8601 timestamp |

### messages

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `from_name` | TEXT | Sender participant |
| `to_name` | TEXT | Recipient participant |
| `body` | TEXT | Message content |
| `type` | TEXT | `ask`, `notify`, `reply`, `command` |
| `status` | TEXT | `pending`, `delivered`, `read` |
| `email_message_id` | TEXT | Email Message-ID (for reply threading) |
| `in_reply_to` | INTEGER | FK to messages.id |
| `created_at` | TEXT | ISO 8601 timestamp |

### events

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `event_type` | TEXT | `register`, `disconnect`, `spawn`, etc. |
| `participant` | TEXT | Who triggered it |
| `summary` | TEXT | Human-readable one-liner |
| `created_at` | TEXT | ISO 8601 timestamp |

## Project Structure

```
claude-email/
├── src/
│   ├── security.py        # Sender validation: From, Return-Path, GPG or shared secret
│   ├── executor.py        # Extract command from body, run claude CLI (shell=False)
│   ├── poller.py          # IMAP4_SSL polling, Message-ID idempotency store
│   ├── mailer.py          # SMTP_SSL reply with threading headers + Message-ID generation
│   ├── chat_db.py         # Shared SQLite layer (WAL mode) — agents, messages, events
│   ├── chat_router.py     # Email-to-chat routing: reply, @agent, meta, CLI fallback
│   ├── chat_handlers.py   # Chat dispatch + relay outbound agent-to-user emails
│   └── spawner.py         # Spawn Claude Code agents, inject MCP config
├── chat/
│   ├── tools.py           # MCP tool implementations (register, ask, notify, check, list, deregister)
│   └── server.py          # MCP SSE server (Starlette + low-level mcp.server)
├── tests/                 # 185 pytest tests (99% coverage)
├── main.py                # Poll loop, signal handling, config from .env, chat integration
├── chat_server.py         # Systemd entry point for claude-chat service
├── install.sh             # Installer: venv + both systemd services
├── claude-email.service   # User-level systemd unit
└── claude-chat.service    # User-level systemd unit (MCP SSE server)
```

## Service Management

```bash
# Status
systemctl --user status claude-chat claude-email

# Restart
systemctl --user restart claude-chat
systemctl --user restart claude-email

# Logs
journalctl --user -u claude-chat -f
journalctl --user -u claude-email -f

# Log file (email service only)
tail -f claude-email.log
```

## Development

```bash
# Run all tests (185 tests, 99% coverage)
.venv/bin/pytest tests/ -q

# Run verbose
.venv/bin/pytest tests/ -v

# Run a specific test file
.venv/bin/pytest tests/test_chat_db.py -v

# Enforce 200-line file limit (also runs in pre-commit hook and CI)
scripts/check-line-limit.sh

# Measure test coverage
.venv/bin/coverage run -m pytest tests/ -q && .venv/bin/coverage report --show-missing
```

## Quality

- **185 tests** with **99% code coverage** across all modules
- **200-line file limit** enforced by automated linter in pre-commit hook and CI
- **Conventional commits** enforced by commit-msg hook
- **Pre-commit testing** — all tests must pass before every commit
- **GitHub Actions CI** — lint + full test suite on every push and PR

## Security

- **Email authentication**: GPG signature or shared secret — no anonymous commands
- **Local MCP**: No authentication on the MCP server. Any localhost process can connect. Acceptable for single-user machines.
- **No shell=True**: All subprocess calls use `shell=False` to prevent command injection
- **Verified TLS**: All IMAP and SMTP connections use `ssl.create_default_context()`
- **No secrets in logs**: Passwords, secrets, and raw command output are never logged
- **Idempotent**: Processed Message-IDs tracked to prevent replay attacks

## Author

**Babak Bandpey** — [cocode.dk](https://cocode.dk) | [LinkedIn](https://linkedin.com/in/babakbandpey) | [GitHub](https://github.com/cocodedk)

## License

Apache-2.0 | (c) 2026 [Cocode](https://cocode.dk) | Created by [Babak Bandpey](https://linkedin.com/in/babakbandpey)
