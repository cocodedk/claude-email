# Bus-driven agent wake — design

Date: 2026-04-20
Branch: `feat/chat-stop-hook` (implementation may land on a follow-up branch)

## Problem

Claude Code hooks (`SessionStart`, `UserPromptSubmit`, `Stop`) only fire on session events. When a registered agent's session is idle — no user prompt, no in-flight response, or no running Claude session at all — a peer message sitting in the bus has no hook to fire on. It stays `pending` indefinitely, and the recipient never sees it.

This is the "true-idle gap" noted in prior memory: architecturally unavoidable with the current hook-only design.

## Goals

1. **claude-email ↔ spawned agent** — the user (via email) can message an agent whose session isn't currently awake; the agent picks it up without manual intervention.
2. **Agent ↔ agent** — one spawned agent can message another whose session isn't awake; the recipient picks it up without manual intervention.
3. **No human-in-the-loop wake** — automation must work while the user is away.

## Non-goals

- Wake guarantees under ~1s latency (this is chat, not RPC).
- Preventing all duplicate delivery under extreme races (the existing `mark_message_delivered` dedupe is sufficient; rare duplicates are tolerable, per prior memory).
- Replacing the three existing hooks — Stop/UserPromptSubmit/SessionStart continue to handle in-session delivery. This mechanism only covers the idle gap.

## Architecture

A background asyncio task runs inside `claude-chat.service`, alongside the MCP Starlette app. It polls the shared `messages` table once per second for rows where `status='pending'` and `to_name` points to a registered agent. When it finds pending mail, it spawns a `claude --print` subprocess in the recipient's `project_path` to drive a turn. The existing `SessionStart` drain hook reads the pending rows as context; the agent responds through normal MCP tools (`chat_notify`, `chat_message_agent`) and the subprocess exits.

Long-lived sessions are achieved by caching a session UUID per agent and using `claude --print --resume <uuid>` for subsequent turns, keeping prompt cache warm. Sessions expire after 15 minutes without activity.

### Why in the same service

- `claude-chat.service` is already the bus authority.
- One process to restart when iterating on this mechanism.
- Shares `ChatDB` connection handling and logging.
- No new systemd unit to install across the ~79 projects.

### Why polling (not in-process event fanout)

Messages enter the `messages` table from two processes: the claude-chat MCP server (agent-to-agent) and claude-email (email-to-agent). An in-process event bus in claude-chat would only catch half the traffic. Polling catches everything uniformly with one code path. SQLite reads on a small, indexed table are ~sub-millisecond — the cost is negligible.

## Components (new)

### `src/wake_watcher.py` (~120 lines)

- `async def run_wake_watcher(db: ChatDB, spawn_fn, stop_event)` — main loop.
- Per-agent `asyncio.Lock` dict to serialize turns for one agent.
- In-memory session-id cache: `dict[agent_name, (session_id, last_turn_at)]`.
- Tick cadence: 1s by default; configurable via env `WAKE_WATCHER_INTERVAL_SECS`.

### `src/wake_spawn.py` (~60 lines)

- `build_wake_cmd(claude_bin, session_id, is_resume, prompt) -> list[str]` — argv builder.
- `async def run_wake_turn(cmd, cwd, timeout) -> int` — subprocess runner with hard timeout (default 300s).
- Keep fully pure/injectable so the watcher loop can be unit-tested with a fake spawn_fn.

### `src/chat_schema.py` — new `wake_sessions` table

```sql
CREATE TABLE IF NOT EXISTS wake_sessions (
  agent_name TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  last_turn_at TEXT NOT NULL
);
```

Persists the session-id across claude-chat.service restarts so warm sessions survive a redeploy. Added via the existing `_MIGRATIONS` list so older DBs upgrade in place.

### `src/chat_db.py` — new methods

- `get_wake_session(agent_name) -> (session_id, last_turn_at) | None`
- `upsert_wake_session(agent_name, session_id)` — sets `last_turn_at = now()`
- `delete_wake_session(agent_name)` — for explicit expiry or on `reap_dead_agents`.

### `chat_server.py` — wire-up

Start `run_wake_watcher` as a background task in the Starlette lifespan. Cancel it on shutdown. No change to MCP tool surface.

## Data flow

1. Any process calls `ChatDB.insert_message(from=X, to=foo, …)` — row lands `status='pending'`.
2. Watcher tick: `SELECT DISTINCT to_name FROM messages WHERE status='pending' AND to_name IS NOT NULL`.
3. For each recipient `foo`:
   - Try `foo_lock.acquire(blocking=False)`. If already held, skip this tick.
   - Look up `project_path` from `agents` table. If unregistered, log once and skip.
   - Read cached `session_id` for `foo` (memory → `wake_sessions` fallback).
   - Build `claude --print [--session-id <new-uuid> | --resume <existing-uuid>] "Handle any pending bus messages."` with `cwd=project_path`.
   - `await run_wake_turn(cmd, cwd, timeout=300)`.
   - On exit: update `last_turn_at`, release lock. If any message for `foo` is still `pending`, next tick fires another turn.

The SessionStart drain hook (existing) consumes all pending rows atomically via `mark_message_delivered`, injects them into the turn as `additionalContext`, and the agent responds normally.

## Error handling

- **Spawn failure** (binary missing, cwd gone, non-zero exit) — log error; increment a per-agent failure counter in memory.
- **Timeout** (300s hard cap) — kill subprocess, release lock, messages stay `pending`, increment failure counter.
- **3 consecutive failures** for one agent — mark *all* currently pending messages for that agent as `status='failed'` (they are blocked by the same problem) and emit an error notification (below). Counter resets on next successful turn.
- **Database error in watcher loop** — log, sleep one tick, continue; never let the loop die.

### Error notification via email relay

Instead of giving claude-chat its own SMTP client, the watcher writes an error message *onto the bus*, targeted at the user's avatar endpoint registered by claude-email. The existing agent→user email relay in `src/chat_handlers.py` picks it up and sends the email. Keeps claude-chat email-agnostic.

Error message body template:

```
[wake-watcher] persistent spawn failure
agent: agent-foo
project: /home/cocodedk/0-projects/foo
stuck messages: 4 (oldest: 2026-04-20T09:12:33Z)
last error: FileNotFoundError: claude binary not found on PATH
```

### Rate limit

At most **one error email per recipient agent per hour**. Tracked in memory (`dict[agent_name, last_error_sent_at]`). Continues to log every failure; only emails are throttled. Lost on restart — acceptable; after a restart the first error re-emails, which is the right behavior.

### Idle expiry

If `now - last_turn_at > 15min` for a cached session-id, drop it. Next arrival creates a fresh session. This prevents unbounded growth of resume history. Expiry runs inline on arrival; no separate sweeper.

## Testing

### Unit (pytest, no subprocess)

- Lock map: acquire/release, non-blocking skip when held, fairness across multiple agents.
- Session-id cache: first arrival creates UUID, second reuses, expiry after 15 min.
- Command builder: first vs resume argv matches snapshot, cwd set correctly.
- Watcher tick: given a fake `spawn_fn` and seeded DB, asserts correct recipient set, correct call args, failure counter behavior, rate-limited error insert.

### Integration (pytest, in-process)

- Real `ChatDB`, fake spawn_fn that marks rows delivered — verify end-to-end: insert → detect → spawn → drain → no loop.
- Error path: spawn_fn raises, assert error message row inserted for user avatar after threshold, rate-limit respected.

### E2E (manual, not in CI)

- Real `claude --print` against a test project directory. Run via a test script; document in `docs/`.

### Coverage

Must maintain 100% coverage per project invariants. `.coveragerc` already omits tests; new modules must hit 100% in the unit suite.

## Configuration

New environment variables (documented in `.env.example` / `README.md`):

- `WAKE_WATCHER_INTERVAL_SECS` — poll cadence, default `1.0`.
- `WAKE_SUBPROCESS_TIMEOUT_SECS` — hard kill after this many seconds, default `300`.
- `WAKE_SESSION_IDLE_EXPIRY_SECS` — drop cached session_id after this, default `900` (15 min).
- `WAKE_MAX_CONSECUTIVE_FAILURES` — error email + drain-oldest after this many, default `3`.
- `WAKE_USER_AVATAR_NAME` — the bus name of the user avatar that claude-email's relay listens on, default `"user"` (matches `chat_handlers.py:relay_outbound_messages` which polls `get_pending_messages_for("user")`).

## Deployment

1. Install: ship new modules as part of the claude-email repo (shared via already-installed `src/` package).
2. Migration: `wake_sessions` table created on next `ChatDB.__init__` via existing `_MIGRATIONS` idempotent path.
3. Restart: `systemctl --user restart claude-chat.service` picks up the new watcher. Already-running agents don't need restart since this mechanism doesn't touch `.claude/settings.json` or hook scripts.
4. Rollback: remove the watcher task startup call in `chat_server.py`; table stays (harmless).

## Open questions

None — all clarifying questions resolved during brainstorm.
