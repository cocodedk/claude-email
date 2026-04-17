# claude-chat — Brainstorm & Design Notes

## What is claude-chat?

A pure **message relay service** (MCP server) that brokers private conversations between participants. It routes messages, manages a participant registry, and persists all events to a shared SQLite database in a pre-formatted, ready-to-serve shape.

**claude-email** is the user's avatar — it represents user@example.com in the chat system, bridges email to MCP, spawns and manages agents, and reads the DB directly for fast status queries.

---

## Architecture

```
SQLite DB (single source of truth — event-driven writes, pre-formatted reads)
    |
    |--- claude-chat (MCP server — writes events, routes messages)
    |--- claude-email (orchestrator — reads/writes DB directly, bridges email)
    |
    |         MCP
    |--- agent-fits
    |--- agent-claude-email
    |--- agent-whatever
```

### claude-chat (the bus)

- Pure MCP server with SSE transport
- Routes messages between participants
- Writes every event to SQLite in a denormalized, ready-to-serve format
- No email logic, no process management — just the message bus

### claude-email (the user / orchestrator)

- Represents user@example.com in the chat system
- Bridges email <-> MCP: incoming mail becomes chat messages, agent messages become outgoing email
- **Spawns agents**: can start Claude Code CLI instances in project directories, preconfigured with claude-chat's MCP server
- **Process management**: tracks spawned agents (PIDs, status) in the DB
- **Reads DB directly** for meta-queries (agent list, status, chat history) — no MCP round-trip, no token waste
- Writes to DB: spawn records, process state, agent lifecycle events

### Agents (peers on the bus)

- Claude Code CLI instances running in project directories
- Connect to claude-chat via MCP
- Auto-register on startup as `agent-<project-folder-name>`
- Talk to "the user" — unaware that email is involved
- Can be spawned idle (waiting for commands) or with an initial instruction

---

## Event-Driven Data Model

The SQLite DB is the **single source of truth**. All writes are event-driven. All data is stored in a format that is **ready to be served without extra processing**.

### Design principles

- **Denormalized**: every row contains everything needed to answer a query — no joins
- **Pre-formatted**: status fields, display names, timestamps are stored in human-readable form
- **Event-driven**: every state change is an insert or update, never a delete
- **Shared access**: both claude-chat and claude-email read/write the same DB (SQLite WAL mode for concurrent access)

### Key tables (conceptual)

**agents**
- `name` — e.g., `agent-fits` (primary key)
- `project_path` — e.g., `/home/cocodedk/0-projects/fits`
- `status` — `running`, `idle`, `disconnected`
- `pid` — OS process ID (if spawned by claude-email)
- `registered_at` — timestamp
- `last_seen_at` — timestamp
- **Never expires** — agents persist until explicitly removed

**messages**
- `id` — auto-increment
- `from_name` — sender participant name
- `to_name` — recipient participant name
- `body` — message text, ready to serve
- `type` — `ask` (blocking), `notify` (fire-and-forget), `reply`, `command`
- `status` — `pending`, `delivered`, `read`
- `email_thread_id` — for email threading (In-Reply-To / References)
- `created_at` — timestamp

**events** (audit log)
- `id` — auto-increment
- `event_type` — `register`, `disconnect`, `spawn`, `message`, `status_change`
- `participant` — who triggered it
- `summary` — human-readable one-liner, ready to serve
- `created_at` — timestamp

---

## Requirements

### Participants

- **Agents**: Claude Code CLI instances in different project directories, and long-running stateful sessions
- **Identity**: Derived from project folder name, prefixed with `agent-` (e.g., `agent-fits`). claude-email registers as the user.
- **Registration**: Agents initiate by calling `chat_register()`. Or claude-email spawns them and they auto-register.
- **Persistence**: Sessions never expire. Chat history and agent identity survive restarts and crashes indefinitely.

### Communication Model

1. **Agent -> User**: Agent sends message via MCP → claude-chat writes to DB → claude-email picks it up → emails user@example.com
2. **User -> Agent (reply)**: user@example.com replies → claude-email receives it → writes to DB via MCP → agent picks it up
3. **User -> System (meta)**: User asks "which agents are running?" → claude-email reads DB directly → replies by email. No MCP, no tokens.
4. **User -> Agent (dispatch)**: User emails "tell agent-fits to do X" → claude-email parses it → routes via MCP → agent receives it
5. **User -> Spawn**: User emails "start agent in /projects/fits" → claude-email spawns the process → agent auto-registers → user can command it

### MCP Tools

- **`chat_register(name, project_path)`** — Register as a participant
- **`chat_ask(message)`** — Blocking. Send a message, wait indefinitely for a reply.
- **`chat_notify(message)`** — Fire-and-forget. Send a message, return immediately.
- **`chat_check_messages()`** — Poll for inbound messages (replies, commands, dispatches).
- **`chat_list_agents()`** — List all registered participants and their status.
- **`chat_deregister()`** — Explicitly leave the chat.

### Transport

- MCP server with SSE transport (multiple participants connect to one server)

### Security

- **Local participants**: Trusted (same machine).
- **Email side**: Handled by claude-email — same auth model as today (authorized sender + GPG/shared secret).

### Agent Spawning

- claude-email can spawn Claude Code CLI instances in any project directory
- Spawned agents are preconfigured with claude-chat's MCP server URL
- Agents auto-register on startup as `agent-<folder-name>`
- Can be spawned idle (wait for commands) or with an initial instruction
- claude-email tracks PIDs and process state in the DB
- User can stop agents via email command

### Timeouts

- `chat_ask` waits **indefinitely** — no timeout. The agent blocks until the user replies.
- Agent sessions **never expire** — they persist until explicitly deregistered or stopped.

### Service Management

claude-email can manage systemd services on the user's behalf:

- **Self-restart**: User emails "restart yourself" → claude-email runs `systemctl --user restart claude-email.service` → process dies, systemd brings it back up.
- **Restart claude-chat**: User emails "restart chat" → claude-email runs `systemctl --user restart claude-chat.service`.
- Both services run as **systemd user services** (`~/.config/systemd/user/`) — no sudo, no privilege escalation needed.
- claude-email manages services it owns under the same user account.

---

## Decision Log

| Question | Decision | Reason |
|----------|----------|--------|
| Agent types | CLI instances + long-running sessions | Both A and B from options |
| How agents join | Agent initiates via MCP tool | User does not initiate |
| Agent identity | `agent-<project-folder>` | Automatic, predictable |
| Blocking vs async | Agent chooses: `ask` (blocks) or `notify` (fire-and-forget) | Maximum flexibility |
| Where service lives | Same repo, separate service | Clean separation |
| Agent-user transport | MCP (SSE) | Native Claude Code support |
| User transport | Email via claude-email | Reuse existing infrastructure |
| Persistence | SQLite, event-driven, denormalized | Ready-to-serve reads, no joins |
| Security (local) | Trusted | Same machine |
| Security (email) | Existing auth model | GPG/shared secret |
| Architecture | claude-chat = bus, claude-email = user/orchestrator | Clean roles |
| Timeouts | None — wait indefinitely | User decides pace |
| Agent lifecycle | Never expire | Explicit removal only |
| Agent spawning | claude-email spawns, agents auto-register | User commands via email |
| Meta-queries | claude-email reads DB directly | No MCP overhead, no tokens |
| DB access pattern | Event-driven writes, pre-formatted reads | Serve without processing |
| Service management | claude-email can restart itself and claude-chat via `systemctl --user` | No sudo — user-level systemd services |

---

## Open Questions (to resolve during design)

- Email threading details: exact mapping from agent conversation to email thread headers
- Subject line format for chat emails (how they appear in the user's inbox)
- How claude-email distinguishes chat replies from direct CLI commands
- Agent spawn command syntax (email format for "start agent in X")
- MCP server configuration: host, port, auth for SSE endpoint
