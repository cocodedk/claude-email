# claude-email

**Website**: [cocodedk.github.io/claude-email](https://cocodedk.github.io/claude-email/) · [فارسی](https://cocodedk.github.io/claude-email/fa/)

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

## Compared to Claude Code's Agent View

Claude Code now ships a built-in [Agent View](https://claude.com/blog/agent-view-in-claude-code) — a terminal-side overview of every concurrent session, with inline replies and `claude --bg` for backgrounded tasks. It is excellent when you are at the laptop.

`claude-email` starts where Agent View stops:

- **Remote-first.** Drive every agent from any inbox — phone, web, `mutt` — without ssh, VPN, or a terminal open. Agent View is local to one machine; an email is not.
- **Inter-agent bus.** Agents talk to *each other* over the MCP chat bus via `chat_message_agent`, not just to you. Agent View has no agent-to-agent channel.
- **Persistent, multi-surface state.** Conversations, task history, and liveness live in SQLite (WAL) and surface on a graphical CRT dashboard at `/dashboard`, the Android companion (in progress), and the same email thread you started in — across reboots and bus restarts.

In short: Agent View is the cockpit when you're at the laptop; `claude-email` is the radio when you're not.

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
- Seven core MCP tools for agent communication (register, ask, notify, **message_agent** for peer-to-peer, check, list, deregister) plus project-scoped tools (spawn, enqueue, cancel, queue_status, reset, commit, where_am_i, retry)
- `chat_ask` blocks for up to one hour waiting for the user's reply
- Agent-to-user messages relayed as emails with proper threading
- User replies routed back to the correct agent via In-Reply-To matching

### Agent Management
- Spawn Claude Code agents in any project directory via email
- Automatic per-project bootstrap: `.mcp.json` declares the chat server and `.claude/settings.json` wires five Claude Code hooks:
  - `SessionStart` runs `scripts/chat-session-start-hook.sh` (pre-registers server-side via `chat-register-self.py` + injects the bus guide from `chat-agent-instruction.txt`) and `scripts/chat-drain-inbox.py` (drains any queued mail into the session's opening context).
  - `UserPromptSubmit` runs `chat-drain-inbox.py` again so every user turn auto-delivers messages that arrived mid-session — messages you send while the agent is idle get picked up on its next turn without relying on the model to poll.
  - `Stop` runs `chat-drain-inbox.py` at the end of every agent response; when peer messages are pending it emits `{"decision":"block","reason":...}`, cancelling the stop and reinjecting the messages as the agent's next turn. Closes the "peer sent something while I was mid-response" gap without polling.
  - `PreCompact` runs `scripts/chat-precompact-hook.py` to log a `hook_precompact` flow event whenever Claude Code rotates its working memory (manual `/compact` or automatic). Best-effort telemetry only — it keeps the dashboard's flow panel pulsing across compaction so a long-running agent doesn't look dead during the gap.
  - `PostToolUse` (matcher `Bash`) runs `scripts/chat-drain-on-bash-commit.sh`, a thin wrapper that pipes the payload to `chat-drain-inbox.py` only when the Bash command begins with `git commit`. Closes the "peer pinged me while I was mid-edit and I just committed" gap without polling, and without firing the drain on every `ls`/`grep`/etc.
- Agent status tracked along two axes for envelope `v >= 2` consumers:
  - **Process-state** (`online` / `stale` / `offline`) — derived from `last_seen_at` heartbeat (5-min window), `is_alive(pid)` when set, and a 30-min ghost threshold.
  - **Task-state** (`waiting` / `working` / `completed` / `error` / `null`) — derived from the tasks table with a configurable fade window (`TASK_STATE_FADE_SEC`, default 30 s).
- Envelope `v: 1` clients keep the legacy 3-state vocabulary (`connected` / `disconnected` / `absent`) and never see `task_state`.
- Agent PIDs recorded in the database

### Idle auto-drain cron (live-but-idle gap)
Hooks only fire on session events — `SessionStart` at boot, `UserPromptSubmit` on each user turn, `Stop` at end-of-response, `PreCompact` at compaction, `PostToolUse` on each `git commit`. A live Claude Code session that's idle between user prompts has no event firing, so peer messages arriving in that window wait for the next user turn. The optional auto-drain cron closes that gap: it fires an `[auto-drain tick]` prompt on a fixed cadence, which triggers `UserPromptSubmit` and drains any queued bus messages into the next turn.

- Default cadence: every 5 minutes (`*/5 * * * *`); adjustable per session via Claude Code's `CronCreate` / `CronDelete` tools.
- Opt-in — not auto-installed by `SessionStart`. Run the `/chat-rejoin` skill to install it (idempotent — skips if already present).
- When the inbox is empty the agent replies `quiet` and burns no further context; queued messages are surfaced verbatim by the same drain script the hooks use.
- Complements the wake watcher below: that one handles "session not running", this one handles "session running but no event has fired recently".

### Wake watcher (idle-agent gap)
Hooks only fire on session events. When a peer message arrives for an agent whose Claude Code session is idle (or not running), there is no hook to fire. The wake watcher lives inside `claude-chat.service`, polls the `messages` table once per second, and spawns a short-lived `claude --print` subprocess in the recipient's `project_path` so the existing `SessionStart` drain hook can surface the queued messages.

- Sessions resume via `claude --print --resume <uuid>` to keep the prompt cache warm across turns; UUID is cached in-memory and persisted in the `wake_sessions` table.
- Turns for the same agent are serialized with an in-memory lock; arrivals during an in-flight turn are picked up on the next tick.
- After 3 consecutive spawn failures for an agent, an error notification is inserted as a bus message to `"user"` — the existing email relay picks it up. Rate-limited to one email per agent per hour.
- Tunable via `WAKE_*` env vars — see `.env.example`.
- Manual smoke test: `scripts/test-wake-smoke.sh agent-smoke /tmp/smoke-wake` (requires running services).

### Test-Sender Isolation (optional)
- Set `TEST_SENDER` in `.env` and copy `.env.test.example` → `.env.test`, then rerun `install.sh`. A second systemd unit `claude-chat-test.service` comes up on port 8421 with its own DB and `CLAUDE_CWD`.
- Emails from `TEST_SENDER` route to the test universe's workers; workers spawned there cannot reach prod projects because they resolve against a different allowed-base. Audit logs, tasks, and agents are physically disjoint.
- Same auth gate (`AUTH:<secret>` or GPG) applies to both senders — test access is not a lower privilege tier; it's a scope tier.

### Task Queue & Branch Safety
- Every task runs on its own branch: `claude/task-<id>-<slug>`. Mainline is never touched by an agent.
- Before starting a task, the worker verifies the project is a clean git checkout. A dirty repo fails the task with the porcelain status so your uncommitted work is never overwritten. Commit or stash first, then re-queue.
- Non-git project folders still run — the branch dance is skipped with a warning.
- After each task terminates, a record is appended to `<project>/.claude/tasks.jsonl` (machine-readable) and `<project>/.claude/CHANGELOG-claude.md` (human-readable). Both files include the branch name, request body, start/end timestamps, and status.
- Suggested `.gitignore` in each project: `.claude/tasks.jsonl` and `CHANGELOG-claude.md` (the `.claude/` dir is usually already ignored for the MCP config).

### Read-only tasks skip the dirty-repo gate

First-time questions and follow-up replies classified as obviously read-only
(`explain …`, `show …`, `list …`, plain interrogatives, polite forms like
`can you explain …`) no longer require a clean working tree and no longer
fork a per-task branch unless they are continuing a prior task branch.
Mutating tasks still require clean for a fresh branch. Classification is
conservative — anything ambiguous (e.g. `also fix the rest`) is treated
as mutating. The classifier is regex-only and runs server-side.

### Email follow-ups continue on the same branch

When you reply on a thread that came from a prior task's result, the
follow-up task reuses the prior task's branch — even if the prior task
left it dirty (uncommitted edits from the previous turn are treated as
*your* work in progress). If the repo has unrelated dirty changes on a
different branch, the follow-up still fails with a clear "cannot switch
safely" message. The lookup walks the SMTP `In-Reply-To` header back
through `outbound_emails.task_id`; pre-existing rows without that
column behave exactly as today.

### Service Management
- Two user-level systemd services (no sudo required)
- Restart either service via email command
- Lingering enabled for headless operation

## Requirements

- Python 3.12+
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
| `AUTHORIZED_SENDER` | Only process emails from this address. Accepts a comma-separated list when one person has several inboxes that should share creds, project base, and conversation state — the first entry is canonical (the default relay recipient), the rest are aliases. | `user@example.com` or `user@example.com,alias@example.com` |
| `EMAIL_DOMAIN` | Domain for Message-ID generation | `example.com` |

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
| `CLAUDE_EMAIL_EXCLUDE_DYNAMIC_PROMPT` | *Optional.* Passes `--exclude-dynamic-system-prompt-sections` to the `claude` CLI, stripping non-deterministic system-prompt sections. Defaults to on; set to `0` to disable. | `0` |
| `CLAUDE_EMAIL_MCP_NONBLOCKING` | *Optional.* Sets `MCP_CONNECTION_NONBLOCKING=true` in the spawned CLI's env so slow or unhealthy MCP servers don't stall startup. Defaults to off; set to `1` to enable. | `1` |

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
| `SHARED_SECRET` | Subject prefix secret — **also required for the JSON envelope path** | `change_this` |
| `GPG_FINGERPRINT` | GPG key fingerprint (enables GPG mode) | |
| `GPG_HOME` | Custom GPG home directory | |

At least one of `SHARED_SECRET` / `GPG_FINGERPRINT` must be set or the service
refuses to start. The two are not interchangeable everywhere: the structured
**JSON envelope** path (used by the companion app) authenticates on `meta.auth`
against `SHARED_SECRET` and never consults GPG. With `SHARED_SECRET` unset that
path fails closed — every envelope is answered
`error.code == "unauthorized"` — because there is no credential to check, not
because none is required. Set `SHARED_SECRET` on the server and in the app to
use it. Plain-text routes are unaffected and keep working under GPG alone.
See [docs/e2e-auth-matrix.md](docs/e2e-auth-matrix.md) for the full
route x condition grid, including the finding that no route enforces a
timestamp freshness window.

**Replay protection.** A captured command mail is single-use *if it is GPG
signed*. The poller keys its idempotency store on two things: the `Message-ID`,
and a fingerprint of the OpenPGP signature the message carries. The second key
is what matters, because no credential this system accepts covers the
`Message-ID` header — without it, an interceptor could rewrite that header (and
the equally unsigned `Date`), re-send the untouched signed payload, and the
command would run again. Routes that authenticate on a bearer value instead — a
thread reply, a reaction, a JSON envelope — bind nothing about the message and
so keep the `Message-ID` store as their only control. See
[docs/e2e-replay.md](docs/e2e-replay.md) for the reasoning, the residual
findings and why no digest fallback was added for the unsigned routes.

**Unsigned headers and routing.** A GPG signature here covers the
`multipart/signed` MIME part and nothing else, so `Subject`, `In-Reply-To`,
`References` and `To` are writable by anyone who can read the mailbox and
re-send — and those headers are exactly what selects the agent, the meta-command
and the thread whose prior turns get prepended to the prompt. Replay protection
is what closes that: a captured payload is single-use, so a mutated re-send is
dropped before any routing code sees it. The router is *not* hardened against
hostile headers; it is never handed a spent credential. See
[docs/e2e-metamorphic-headers.md](docs/e2e-metamorphic-headers.md) for the
mutant set, the reversion evidence and the residual coupling.

**Duplicate and concurrent delivery.** Commands that arrive at the same moment
do not interfere: two different commands delivered simultaneously both run, and
a command delivered twice — whether as the same signed credential under two
different `Message-ID`s, or as the same message re-delivered byte-for-byte by
the mail server — runs exactly once. The poller deduplicates inside a single
poll batch as well as across batches, which matters because the persisted
idempotency store is only written once a message has been fully handled: without
the in-batch check, two copies fetched in the same cycle would both execute.
See [docs/e2e-concurrency.md](docs/e2e-concurrency.md) for how the parallel
delivery is forced and proved, and for which barrier holds which case.

## Sending Commands

### Direct CLI Commands

**GPG mode** (recommended): compose a GPG-signed email to the service address. Subject can be anything.

**Shared secret mode**: set Subject to `AUTH:<secret> <command>`. Email body contains the detailed instruction.

**Subject fallback**: when the body is empty (or only quoted-reply trailer), the Subject is used as the command. Phone clients that compose subject-only mails work without ceremony — `Re: ` / `Fwd: ` / `Fw: ` prefixes are stripped automatically and RFC 2047 encoded-word Subjects (Persian, accents, etc.) are decoded before execution.

In **GPG mode** the Subject fallback is disabled — the GPG signature only covers the message body, so a header-tampering hop could substitute the Subject without invalidating the signature. Put your command inside the signed body.

### Chat Commands

| Command | Description | Example Subject |
|---|---|---|
| `@agent-name <instruction>` | Send instruction to a specific agent | `AUTH:secret @agent-fits run the tests` |
| `status` | List all registered agents and their state | `AUTH:secret status` |
| `spawn <name-or-path> [instruction]` | Spawn an agent. Bare names resolve against `CLAUDE_CWD`; absolute paths also accepted. The agent's bus name defaults to `agent-<basename(path)>`. | `AUTH:secret spawn babakcast` |
| `spawn <name-or-path> as <agent-name> [instruction]` | Spawn under an explicit `agent-name` instead of the cwd-derived default. Use this to run **multiple agents in the same project** (e.g. one main, one optimizer). Names must match `^agent-[a-z0-9][a-z0-9_-]{0,57}$`. | `AUTH:secret spawn babakcast as agent-bc-optimizer` |
| `restart chat` | Restart the claude-chat service | `AUTH:secret restart chat` |
| `restart self` | Restart the claude-email service | `AUTH:secret restart self` |

> **Multi-agent per project.** Both spawn forms set the `CLAUDE_AGENT_NAME` env var on the spawned `claude` process. The SessionStart hook reads it (validated against the regex above) so each session registers under the right name even when N agents share a project directory. Manually-launched sessions can also export `CLAUDE_AGENT_NAME=agent-foo` before running `claude` to claim a non-default name. Note: nested `claude` sessions inherit `CLAUDE_AGENT_NAME` from their parent — `unset` it before starting an unrelated agent in a different project.

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

The agent is pre-registered by the `SessionStart` hook before its first turn (via `scripts/chat-register-self.py` writing the row directly to the DB), so it never needs to call `chat_register` itself — it just uses the tools:

```text
You: Ask the user if the tests should include integration tests.

Claude Code:
  1. Calls chat_ask(_caller="agent-myproject", message="Should I include integration tests in the test suite?")
  2. Blocks until the user replies via email
  3. Receives { reply: "Yes, include integration tests for the API endpoints" }
  4. Continues working with that answer
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
| `chat_register` | Register as a participant (name + project path). Normally called server-side by the `SessionStart` hook; agents don't need to call it themselves. | No |
| `chat_ask` | Send a question to the user and wait for reply | Yes |
| `chat_notify` | Send a fire-and-forget status update to the user | No |
| `chat_message_agent` | Send a one-way notification to another registered agent (peer-to-peer). Rejects unknown recipients and `user` (use `chat_notify` for that). | No |
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
| `chat_commit_project` | Escape hatch for a dirty repo — runs `git add -A && git commit -m <message>` without starting a claude subprocess. Optional `push=true` also runs `git push`, so "commit and push" stays a single tool call instead of falling through to a per-task branch. | No |
| `chat_retry_task` | Re-enqueue a previously terminated task (done/failed/cancelled). Pass `new_body` to refine the instruction. Records the chain via `retry_of`. | No |

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
│   ├── secret_redact.py   # Scrub the shared secret from every outbound mail (subject, body, headers)
│   ├── executor.py        # Extract command from body, run claude CLI (shell=False)
│   ├── poller.py          # IMAP4_SSL polling, idempotency + replay store
│   ├── replay_guard.py    # Content-bound replay key (digest of the OpenPGP signature)
│   ├── mailer.py          # SMTP_SSL reply with threading headers + Message-ID generation
│   ├── chat_db.py         # Shared SQLite layer (WAL mode) — agents, messages, events
│   ├── chat_router.py     # Email-to-chat routing: reply, @agent, meta, CLI fallback
│   ├── chat_handlers.py   # Chat dispatch + relay outbound agent-to-user emails
│   ├── dashboard_queries.py  # Read-only ChatDB projections for the dashboard
│   └── spawner.py         # Spawn Claude Code agents, inject MCP config
├── chat/
│   ├── tools.py                 # MCP tool implementations
│   ├── server.py                # MCP SSE server (Starlette + low-level mcp.server)
│   ├── dashboard.py             # Dashboard HTTP routes + SSE message stream
│   ├── dashboard_page.py        # SVG radar page skeleton
│   ├── dashboard_css.py         # CSS concatenator (shell + graph)
│   ├── dashboard_css_shell.py   # Body/topbar/typography + CRT overlays
│   ├── dashboard_css_graph.py   # Radar, nodes, edges, pulses, feed styles
│   ├── dashboard_js.py          # JS concatenator (graph + stream)
│   ├── dashboard_js_graph.py    # Node positioning, edges, pulse animation
│   └── dashboard_js_stream.py   # Fetch + SSE + entry rendering
├── tests/                 # 1713 unit tests (100% coverage)
│   └── e2e/               # 92 docker-gated end-to-end tests — real stack, zero mocks
├── main.py                # Poll loop, signal handling, config from .env, chat integration
├── chat_server.py         # Systemd entry point for claude-chat service
├── install.sh             # Installer: venv + both systemd services
├── claude-email.service   # User-level systemd unit
└── claude-chat.service    # User-level systemd unit (MCP SSE server)
```

## Live Dashboard — CRT Observatory

`claude-chat` serves a fully-graphic single-page dashboard at
`http://127.0.0.1:$CHAT_PORT/dashboard` (default
`http://127.0.0.1:8420/dashboard`). It visualises **who talks to whom** in
real time as a node-graph, not a text timeline.

- **Radar stage** — the user sits at the centre as a phosphor node; registered agents orbit on a ring sized to the roster. A slow sweep gradient rotates under scan-lines and film noise for a green-phosphor CRT feel.
- **Message pulses** — each bus message fires an easing dot along the chord from sender to recipient, coloured by the sender's hash-derived hue. The target node's halo briefly blooms on arrival.
- **Persistent heat edges** — every `(from, to)` pair accumulates a bowed arc; stroke width and opacity scale logarithmically with volume, so high-traffic channels stand out.
- **Transmission feed** — a CRT-styled log on the side; click any row to expand the full body, coloured by the same per-agent hue.
- **Filter by click** — clicking an agent node narrows the feed to that agent's traffic and dims the other nodes.
- **Live stream** — `EventSource` on `/events` pushes new messages the instant they land in SQLite; the page auto-reconnects on disconnect.
- **Top bar** — UTC clock, operator count, running event counter, and a `LINK LIVE` LED. Typography: `Major Mono Display` for headers, `IBM Plex Mono` for body.
- **Flow view** — a topbar toggle flips the stage from the live observatory to a technical-flow diagram that traces how a peer message reaches an idle agent through the two internal code paths: the Stop-hook self-poll (the agent drains its own inbox at end-of-turn) and the wake_watcher cold-spawn (a fresh CLI is booted so its `SessionStart` hook can drain). The panel is **live**: `wake_spawn_start`/`wake_spawn_end` emit from `wake_watcher`, `hook_drain_stop`/`hook_drain_session` emit from `chat-drain-inbox.py`, `hook_precompact` emits a heartbeat from `chat-precompact-hook.py` across compaction, and each event lights up the matching step card on the diagram.
- **Glossary view** — a third topbar toggle opens a searchable, click-to-expand index of every acronym and term the project uses (MCP, SSE, WAL, IMAP, PPID, Stop hook, nudge Event, …). The search input filters entries in-place across categories.
- **Ghost filter** — agents whose `last_seen_at` is older than 30 minutes are dropped from the dashboard projection. Keeps stale pid=NULL MCP registrations (that `reap_dead_agents` can't see) off the radar.

The page is composed from nine ~100-line modules
(`dashboard_page.py`, `dashboard_css{_shell,_graph}.py`,
`dashboard_flow_{svg,css}.py`, `dashboard_js{_graph,_stream}.py`, plus two
concatenators) so every file stays under the 200-line cap.

All routes are read-only and bind to the same host/port as the MCP server —
127.0.0.1 by default, so access stays local. Tune the poll cadence with
`DASHBOARD_POLL_SECS` (default `1.0`).

## Mobile Companion (Android)

A native Android app is being built in a separate companion repository so
you can dispatch commands and reply to agents from a phone without
composing them in a generic mail client. The app speaks the same
IMAP/SMTP backbone, so the server side requires no changes.

- Repository: *link pending — to be added here once the repo is public.*
- Screenshots: *coming soon — will be embedded below as the app reaches usable milestones.*

<!-- TODO: replace with real repo link + <img> tags or a linked gallery. -->
<!-- Example layout:
| Inbox | Compose | Agent chat |
| :---: | :-----: | :--------: |
| ![inbox](docs/android/inbox.png) | ![compose](docs/android/compose.png) | ![chat](docs/android/chat.png) |
-->


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
# Run all tests (1805 tests, 100% coverage on production code)
.venv/bin/pytest tests/ -q

# Unit tests only — no docker needed
.venv/bin/pytest tests/ -q -m "not e2e"

# End-to-end only — starts a real mail server in docker (see docs/e2e-testing.md).
# Skips with a reason, rather than failing, when docker is unavailable.
.venv/bin/pytest tests/ -q -m e2e

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

- **1805 tests** — 1713 unit tests with **100% code coverage** across all modules, plus 92 docker-gated end-to-end tests
- **200-line file limit** enforced by automated linter in pre-commit hook and CI
- **Conventional commits** enforced by commit-msg hook
- **Pre-commit testing** — all tests must pass before every commit
- **GIT_* isolation** — `tests/conftest.py` unsets `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE` and friends before any test runs. Git exports these when it invokes the pre-commit hook, and inside a linked worktree they are absolute paths into the real repository; `GIT_DIR` overrides a subprocess's `cwd`, so without the scrub a test operating on its own `tmp_path` repo would rewrite the real one's config, HEAD and refs. `GIT_AUTHOR_*` / `GIT_COMMITTER_*` are left alone — tests set those on purpose.
- **GitHub Actions CI** — lint + full test suite on every push and PR

## Security

- **Email authentication**: GPG signature or shared secret — no anonymous commands
- **The shared secret never leaves**: `src/secret_redact.py` scrubs it from every outbound mail — subject, body and *all* headers — at the single choke point every reply passes through (`src/mailer.send_reply`). It matters most in the Subject: `AUTH:<secret> <command>` is a supported auth route, and replies are threaded on the inbound Subject, so without the scrub the credential shipped back out in every `[Running]` and `[Result]`. The scrub covers the bare secret as well as the `AUTH:` token, sees through RFC 2047 encoded-words, and covers `In-Reply-To` / `References` (copied verbatim from the inbound `Message-ID`). A thread whose own `Message-ID` contained the secret loses its threading headers — that is the intended trade.
- **Local MCP**: No authentication on the MCP server. Any localhost process can connect. Acceptable for single-user machines.
- **No shell=True**: All subprocess calls use `shell=False` to prevent command injection
- **Verified TLS**: All IMAP and SMTP connections use `ssl.create_default_context()`
- **No secrets in logs**: Passwords, secrets, and raw command output are never logged
- **Idempotent**: Processed Message-IDs tracked to prevent replay attacks

## Author

**Babak Bandpey** — [example.com](https://example.com) | [LinkedIn](https://linkedin.com/in/babakbandpey) | [GitHub](https://github.com/cocodedk) | [Project site](https://cocodedk.github.io/claude-email/)

## License

Apache-2.0 | (c) 2026 [Cocode](https://example.com) | Created by [Babak Bandpey](https://linkedin.com/in/babakbandpey)
