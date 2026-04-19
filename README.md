# claude-email

An email-driven wrapper for the [Claude Code CLI](https://claude.ai/code) with an integrated chat relay for managing multiple Claude Code agents. Polls an IMAP mailbox for commands, executes them via `claude --print`, and replies via SMTP. Includes a full MCP-based chat system where `claude-email` acts as the user's avatar, brokering conversations between the user (via email) and multiple Claude Code agents (via MCP tools).

## How It Works

```
                         ┌──────────────┐
                         │  User Email  │
                         │ user@example.com │
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
- `chat_ask` blocks for up to one hour waiting for the user's reply
- Agent-to-user messages relayed as emails with proper threading
- User replies routed back to the correct agent via In-Reply-To matching

### Agent Management
- Spawn Claude Code agents in any project directory via email
- Automatic per-project bootstrap: `.mcp.json` declares the chat server and `.claude/settings.json` wires two Claude Code hooks:
  - `SessionStart` runs `scripts/chat-session-start-hook.sh` (pre-registers server-side via `chat-register-self.py` + injects the bus guide from `chat-agent-instruction.txt`) and `scripts/chat-drain-inbox.py` (drains any queued mail into the session's opening context).
  - `UserPromptSubmit` runs `chat-drain-inbox.py` again so every user turn auto-delivers messages that arrived mid-session — messages you send while the agent is idle get picked up on its next turn without relying on the model to poll.
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

After install (or after moving the repo), run `scripts/install-chat-mcp.py <projects-base-dir>` once to bootstrap `.mcp.json` and `.claude/settings.json` in every project directory that should participate on the chat bus. Both files are gitignored per-project and host-specific — the SessionStart hook's command path is resolved from this repo's location at install time.

## Configuration (.env)

Every config value is read from `.env` — no hardcoded defaults in code.

### Email

| Variable | Description | Example |
|---|---|---|
| `IMAP_HOST` | IMAP server hostname | `imap.one.com` |
| `IMAP_PORT` | IMAP server port | `993` |
| `SMTP_HOST` | SMTP server hostname | `send.one.com` |
| `SMTP_PORT` | SMTP server port | `465` |
| `EMAIL_ADDRESS` | IMAP/SMTP account | `agent@example.com` |
| `EMAIL_PASSWORD` | Account password | |
| `AUTHORIZED_SENDER` | Only process emails from this address | `user@example.com` |
| `EMAIL_DOMAIN` | Domain for Message-ID generation | `cocode.dk` |

### Polling & CLI

| Variable | Description | Example |
|---|---|---|
| `POLL_INTERVAL` | Seconds between IMAP polls | `15` |
| `CLAUDE_TIMEOUT` | Max seconds for CLI execution | `300` |
| `CLAUDE_BIN` | Path to Claude CLI binary | `/home/user/.local/bin/claude` |
| `CLAUDE_CWD` | Working directory for CLI commands; also the allowed base for spawn paths — bare names resolve against it, absolute paths must resolve under it. | `/home/user/projects` |
| `STATE_FILE` | Message-ID idempotency store | `processed_ids.json` |
| `CLAUDE_MODEL` | *Optional.* Model alias (`sonnet`, `haiku`) or full name. Leave unset for auto-mode. | `claude-sonnet-4-6` |
| `CLAUDE_EFFORT` | *Optional.* Thinking effort: `low`, `medium`, `high`, `xhigh`, `max`. | `low` |
| `CLAUDE_MAX_BUDGET_USD` | *Optional.* Dollar cap for `--print` calls. Only bites under API-key auth; subscription calls ignore it. | `1.00` |
| `LLM_ROUTER` | *Optional, experimental.* When `1`, the CLI-fallback claude gets a system prompt describing `chat_spawn_agent`, so natural-language bodies like "implement tests in test-01" can spawn agents. Leave blank for deterministic keyword-only routing. | `1` |

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
| `spawn <name-or-path> [instruction]` | Spawn an agent. Bare names resolve against `CLAUDE_CWD`; absolute paths also accepted. | `AUTH:secret spawn babakcast` |
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

## Connecting Claude Code to the Chat Server

When you spawn an agent via the `spawn` email command, the MCP config is injected automatically into the project's `.mcp.json`. To connect a Claude Code session manually, add the chat server to `.mcp.json` in your project root:

```json
{
  "mcpServers": {
    "claude-chat": {
      "url": "http://127.0.0.1:8420/sse"
    }
  }
}
```

Replace the URL with your `CHAT_URL` from `.env`. Once configured, Claude Code discovers the MCP server on startup and gains access to the chat tools listed below.

### Using the chat tools from Claude Code

After connecting, the agent should register itself, then use the tools to communicate:

```
You: Register as an agent and ask the user if the tests should include integration tests.

Claude Code:
  1. Calls chat_register(name="agent-myproject", project_path="/home/user/myproject")
  2. Calls chat_ask(message="Should I include integration tests in the test suite?")
  3. Blocks until the user replies via email
  4. Receives { reply: "Yes, include integration tests for the API endpoints" }
  5. Continues working with that answer
```

Agents can also send fire-and-forget status updates:

```
Claude Code:
  Calls chat_notify(message="All 42 tests passing. Build complete.")
  → User receives an email with the status update
```

### Automatic vs manual setup

| Method | How | When |
|---|---|---|
| **Automatic** | `spawn /path/to/project` via email | Creates agent, injects `.mcp.json`, starts Claude Code |
| **Manual** | Add `.mcp.json` yourself, start `claude` | For existing sessions or custom setups |

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
| `chat_spawn_agent` | Start a new Claude Code agent in a project folder (resolved against `CLAUDE_CWD`) | No |
| `chat_enqueue_task` | Queue a task for a project. Spawns a per-project worker on demand (one per canonical path) that drains the queue in `(priority DESC, id ASC)` order — priority 0..10, anything higher is clamped. Each task runs as `claude --continue --print` so context persists across tasks in the same project. | No |
| `chat_cancel_task` | Cancel the running task for a project (SIGTERM, 10s grace, SIGKILL). Optional `drain_queue=true` also drops pending tasks. | No |
| `chat_queue_status` | Return the running task and pending queue for a project. | No |
| `chat_reset_project` | Step 1 of destructive reset — returns a `confirm_token` valid for 5 minutes. | No |
| `chat_confirm_reset` | Step 2 — consumes the token and runs `git reset --hard HEAD && git clean -fd`, cancels running task, drains queue. | No |
| `chat_where_am_i` | Cross-project dashboard: one row per project with running task, pending count, worker pid, last activity timestamp. | No |

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
├── tests/                 # 479 pytest tests (100% coverage)
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
# Run all tests (479 tests, 100% coverage)
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

- **479 tests** with **100% code coverage** across all modules
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
