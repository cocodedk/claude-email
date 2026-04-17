# claude-chat — Design Spec

**Date**: 2026-04-16
**Status**: Draft
**Author**: bb@cocode.dk + Claude

---

## 1. Overview

claude-chat is a **message relay service** that brokers private conversations between the user (bb@cocode.dk) and multiple Claude Code agents. It runs as a standalone MCP server with an SQLite backend.

claude-email acts as the user's avatar — it bridges email to MCP, spawns and manages agents, queries the DB directly for status, and can restart both services.

```
SQLite DB (single source of truth — WAL mode)
    |
    |--- claude-chat (MCP server — writes events, routes messages)
    |--- claude-email (orchestrator — reads/writes DB, bridges email)
    |
    |         MCP/SSE
    |--- agent-fits
    |--- agent-claude-email
    |--- agent-whatever
```

---

## 2. Components

### 2.1 claude-chat (the bus)

**Role**: Pure message relay. Routes messages between participants, persists all events to SQLite.

**What it does**:
- Runs an MCP server with SSE transport on a local port (default `localhost:8420`)
- Accepts connections from any local process (trusted — same machine)
- Exposes MCP tools for registration, messaging, and status queries
- Writes every event to SQLite in denormalized, ready-to-serve format
- Runs as a user-level systemd service (`claude-chat.service`)

**What it does NOT do**:
- No email logic
- No process management
- No authentication (local = trusted)

### 2.2 claude-email (the user / orchestrator)

**Role**: The user's avatar in the chat system. Bridges email to MCP. Spawns and manages agents.

**Existing functionality** (unchanged):
- Polls IMAP for emails from bb@cocode.dk
- Validates sender (From, Return-Path, GPG/shared secret)
- Executes direct CLI commands via `claude --print`
- Replies via SMTP with threading headers

**New functionality** (added):
- Connects to claude-chat as an MCP client
- Routes chat messages: email → MCP and MCP → email
- Distinguishes chat replies from CLI commands via `In-Reply-To` header matching against known chat Message-IDs in the DB
- Spawns Claude Code agents in project directories
- Tracks spawned agent PIDs and process state in the DB
- Reads DB directly for meta-queries (agent list, status, history)
- Parses structured commands: `@agent-name <instruction>`
- Can restart itself and claude-chat via `systemctl --user restart`

### 2.3 Agents (peers on the bus)

**Role**: Claude Code CLI instances that talk to the user through the chat.

- Connect to claude-chat via MCP
- Auto-register on startup as `agent-<project-folder-name>`
- Use `chat_ask` (blocking) and `chat_notify` (fire-and-forget) to communicate
- Poll for unsolicited messages via `chat_check_messages`
- Unaware that email is involved — they just talk to "the user"

---

## 3. MCP Tools

All tools are exposed by claude-chat's MCP server. Used by both agents and claude-email.

### 3.1 `chat_register`

Register as a participant in the chat system.

```
Parameters:
  name: string          # e.g., "agent-fits"
  project_path: string  # e.g., "/path/to/projects/fits"

Returns:
  { "status": "registered", "name": "agent-fits" }
```

- Inserts or updates the `agents` table
- Sets status to `running`, records `registered_at` and `last_seen_at`
- If the agent was previously registered (persistent session), it reconnects

### 3.2 `chat_ask`

Send a message to the user and block until a reply is received.

```
Parameters:
  message: string       # The question or request

Returns:
  { "reply": "user's response text" }
```

- Inserts a message row with type `ask`, status `pending`
- Holds the MCP tool response open until a reply message appears in the DB
- If the SSE connection drops, the message stays `pending` — the agent picks up the reply via `chat_check_messages` after reconnecting
- No timeout — waits indefinitely

### 3.3 `chat_notify`

Send a fire-and-forget message to the user.

```
Parameters:
  message: string       # Status update, FYI, etc.

Returns:
  { "status": "sent" }
```

- Inserts a message row with type `notify`, status `pending`
- Returns immediately — does not wait for a reply
- claude-email picks it up and sends as email

### 3.4 `chat_check_messages`

Poll for inbound messages (replies, commands, dispatched instructions).

```
Parameters:
  (none)

Returns:
  { "messages": [ { "id": 1, "from": "user", "body": "...", "type": "reply", "created_at": "..." }, ... ] }
```

- Returns all undelivered messages for the calling participant
- Marks returned messages as `delivered`
- Updates `last_seen_at` on the agent record

### 3.5 `chat_list_agents`

List all registered participants and their status.

```
Parameters:
  (none)

Returns:
  { "agents": [ { "name": "agent-fits", "status": "running", "project_path": "...", "last_seen_at": "..." }, ... ] }
```

### 3.6 `chat_deregister`

Explicitly leave the chat.

```
Parameters:
  (none)

Returns:
  { "status": "deregistered" }
```

- Sets agent status to `deregistered`
- Does NOT delete the record — history is preserved

---

## 4. Data Model

SQLite database with WAL mode enabled and a busy timeout of 5000ms for safe concurrent access by claude-chat and claude-email.

All data is **denormalized and pre-formatted** — every row is ready to serve without joins or post-processing.

### 4.1 `agents` table

| Column | Type | Description |
|--------|------|-------------|
| `name` | TEXT PK | e.g., `agent-fits` |
| `project_path` | TEXT | Absolute path to project directory |
| `status` | TEXT | `running`, `idle`, `disconnected`, `deregistered` |
| `pid` | INTEGER NULL | OS process ID (if spawned by claude-email) |
| `registered_at` | TEXT | ISO 8601 timestamp |
| `last_seen_at` | TEXT | ISO 8601 timestamp |

- Agents **never expire** — rows persist until explicitly deregistered
- `last_seen_at` updated on every `chat_check_messages` call

### 4.2 `messages` table

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment |
| `from_name` | TEXT | Sender participant name |
| `to_name` | TEXT | Recipient participant name |
| `body` | TEXT | Message text, ready to serve |
| `type` | TEXT | `ask`, `notify`, `reply`, `command` |
| `status` | TEXT | `pending`, `delivered`, `read` |
| `email_message_id` | TEXT NULL | Email Message-ID (for threading) |
| `in_reply_to` | INTEGER NULL | FK to messages.id (for conversation threading) |
| `created_at` | TEXT | ISO 8601 timestamp |

### 4.3 `events` table

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment |
| `event_type` | TEXT | `register`, `disconnect`, `spawn`, `stop`, `message`, `restart` |
| `participant` | TEXT | Who triggered it |
| `summary` | TEXT | Human-readable one-liner, ready to serve |
| `created_at` | TEXT | ISO 8601 timestamp |

---

## 5. Message Flows

### 5.1 Agent asks user a question

1. Agent calls `chat_ask("Should I refactor the auth module?")`
2. claude-chat inserts message row: `from=agent-fits, to=user, type=ask, status=pending`
3. claude-chat inserts event: `message from agent-fits: Should I refactor...`
4. claude-chat holds the tool response open
5. claude-email polls `chat_check_messages()` (or reads DB directly)
6. claude-email sends email to bb@cocode.dk:
   - Subject: `[agent-fits] Should I refactor the auth module?`
   - From: `claude@cocode.dk`
   - Stores the email's Message-ID in the `email_message_id` column
7. User replies by email
8. claude-email receives reply, matches `In-Reply-To` header against known `email_message_id` in DB
9. claude-email identifies this as a reply to agent-fits's ask
10. claude-email inserts reply message: `from=user, to=agent-fits, type=reply`
11. claude-chat detects the reply, returns it as the `chat_ask` tool response
12. Agent receives: `{ "reply": "Yes, go ahead and use the new pattern from fits" }`

### 5.2 Agent sends a status update

1. Agent calls `chat_notify("Refactoring complete, 12 files changed")`
2. claude-chat inserts message row: `type=notify, status=pending`
3. Tool returns immediately: `{ "status": "sent" }`
4. claude-email picks up the message, emails it to bb@cocode.dk
5. User reads it — no reply needed

### 5.3 User dispatches a command to an agent

1. User emails: `AUTH:<secret> @agent-fits refactor the auth module using the new pattern`
2. claude-email validates auth, detects `@agent-fits` prefix
3. claude-email inserts message: `from=user, to=agent-fits, type=command, body="refactor the auth module using the new pattern"`
4. Agent calls `chat_check_messages()` — receives the command
5. Agent executes the instruction

### 5.4 User asks for status (meta-query)

1. User emails: `AUTH:<secret> status`
2. claude-email validates auth, recognizes meta-command
3. claude-email queries `agents` table directly — no MCP round-trip
4. claude-email replies:
   ```
   Active agents:
   - agent-fits: running (last seen 2m ago)
   - agent-claude-email: running (last seen 5m ago)
   ```

### 5.5 User spawns a new agent

Spawn idle:
1. User emails: `AUTH:<secret> spawn /path/to/projects/fits`

Spawn with initial instruction:
1. User emails: `AUTH:<secret> spawn /path/to/projects/fits refactor the auth module`

In both cases:
2. claude-email validates auth, recognizes spawn command
3. claude-email writes/updates `.mcp.json` in `/path/to/projects/fits` with claude-chat server config
4. claude-email spawns: `claude --print` (or interactive session) in that directory
5. claude-email records PID and agent info in `agents` table
6. Spawned agent starts, reads `.mcp.json`, connects to claude-chat, calls `chat_register("agent-fits", "/path/to/projects/fits")`
7. claude-email replies: `Agent agent-fits spawned and registered`

### 5.6 User restarts a service

1. User emails: `AUTH:<secret> restart chat`
2. claude-email validates auth, recognizes restart command
3. claude-email runs: `systemctl --user restart claude-chat.service`
4. claude-email replies: `claude-chat restarted`

For self-restart:
1. User emails: `AUTH:<secret> restart self`
2. claude-email runs: `systemctl --user restart claude-email.service`
3. (No reply — process dies and restarts. User can check status later.)

---

## 6. Email Threading

Each agent gets its own email thread. The mapping:

- When claude-email sends an agent's first message, it creates a new email thread (unique Subject + Message-ID)
- Subject format: `[agent-name] first few words of message...`
- Subsequent messages in the same agent conversation use `In-Reply-To` and `References` headers pointing to the previous message in that thread
- The `email_message_id` column in the `messages` table tracks the mapping
- When a reply comes in, claude-email matches `In-Reply-To` against the DB to identify the agent

---

## 7. Chat-vs-CLI Routing

claude-email must distinguish between:
- **Direct CLI commands**: `AUTH:<secret> <command>` — executed via `claude --print` as today
- **Chat replies**: emails whose `In-Reply-To` header matches a known chat `email_message_id` in the DB
- **Chat commands**: `AUTH:<secret> @agent-name <instruction>` — dispatched to an agent
- **Meta-commands**: `AUTH:<secret> status`, `spawn`, `restart`, etc.

**Routing priority** (checked in order):
1. If `In-Reply-To` matches a chat Message-ID in DB → chat reply, route to agent
2. If subject (after AUTH prefix) starts with `@agent-` → dispatch command to agent
3. If subject (after AUTH prefix) matches a meta-command (`status`, `spawn`, `restart`) → handle internally
4. Otherwise → direct CLI command (existing behavior, unchanged)

---

## 8. Agent Spawning

### 8.1 MCP config injection

Before spawning an agent, claude-email writes or updates `.mcp.json` in the target project directory:

```json
{
  "mcpServers": {
    "claude-chat": {
      "url": "http://localhost:8420/sse"
    }
  }
}
```

This is how Claude Code natively discovers MCP servers.

### 8.2 Spawn process

1. Write `.mcp.json` to project directory
2. Spawn `claude` process in the project directory
3. Record PID in `agents` table with status `running`
4. The agent reads `.mcp.json`, connects to claude-chat, calls `chat_register()`

### 8.3 Process tracking

- claude-email periodically checks if spawned PIDs are still alive
- If a PID dies, update agent status to `disconnected` in the DB
- User can restart dead agents via email command

---

## 9. Persistence & Concurrency

### 9.1 SQLite configuration

- **WAL mode**: Enables concurrent reads from claude-email while claude-chat writes
- **Busy timeout**: 5000ms — retries on lock contention
- **Journal**: WAL (write-ahead log)
- **DB location**: `__INSTALL_DIR__/claude-chat.db`

### 9.2 Shared access pattern

- **claude-chat writes**: agent registration, messages, events
- **claude-email writes**: spawn records, PID updates, process state
- **claude-email reads**: status queries, message routing, In-Reply-To matching
- Both processes use the same DB file — WAL mode makes this safe

### 9.3 Data retention

- Agent records: never deleted, never expire
- Messages: never deleted — full history preserved
- Events: never deleted — full audit log

---

## 10. Systemd Services

### 10.1 claude-chat.service

```ini
[Unit]
Description=Claude Chat Relay (MCP Server)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=__INSTALL_DIR__
EnvironmentFile=__INSTALL_DIR__/.env
ExecStart=__INSTALL_DIR__/.venv/bin/python3 __INSTALL_DIR__/chat_server.py
Restart=on-failure
RestartSec=10
TimeoutStopSec=30

[Install]
WantedBy=default.target
```

Installed to `~/.config/systemd/user/claude-chat.service`.

### 10.2 claude-email.service

Existing service (already converted to user-level). Gets additional MCP client functionality but the service file stays the same.

### 10.3 Startup order

claude-chat should start before claude-email. Add to claude-email.service:

```ini
[Unit]
After=claude-chat.service
Wants=claude-chat.service
```

---

## 11. Project Structure

```
claude-email/
├── src/
│   ├── security.py        # Existing — sender validation
│   ├── executor.py        # Existing — command execution
│   ├── poller.py          # Existing — IMAP polling
│   ├── mailer.py          # Existing — SMTP reply
│   ├── chat_client.py     # NEW — MCP client for claude-email
│   ├── chat_router.py     # NEW — routing logic (chat vs CLI vs meta)
│   ├── chat_db.py         # NEW — shared SQLite access layer
│   └── spawner.py         # NEW — agent process spawning
├── chat/
│   ├── server.py          # NEW — MCP server (claude-chat main)
│   ├── tools.py           # NEW — MCP tool implementations
│   └── db.py              # NEW — server-side DB writes
├── tests/
│   ├── (existing 34 tests)
│   ├── test_chat_*.py     # NEW — chat tests
│   └── test_spawn_*.py    # NEW — spawner tests
├── main.py                # Existing — claude-email entry point (modified)
├── chat_server.py         # NEW — claude-chat entry point
├── install.sh             # Updated — installs both services
├── claude-email.service   # Existing (updated with After=claude-chat)
├── claude-chat.service    # NEW
└── claude-chat.db         # Runtime — SQLite database
```

---

## 12. Security

- **Local MCP**: No authentication. Any process on localhost can connect. Acceptable for single-user machine.
- **Email**: Existing auth model — authorized sender + GPG/shared secret. No changes.
- **Subprocess**: All spawned processes use `shell=False`. No command injection.
- **DB**: File permissions — readable/writable only by the owning user.
- **No secrets in logs**: Existing policy applies to all new code.

---

## 13. Out of Scope

- Multi-user support (only bb@cocode.dk)
- Remote agents (all agents run on the same machine)
- Web UI or dashboard
- Message encryption between participants (local = trusted)
- Agent-to-agent direct communication (all messages go through the user)
