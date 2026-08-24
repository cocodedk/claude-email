# CLAUDE.md — claude-email

## Project Overview

Email-driven wrapper for the Claude Code CLI with an integrated chat relay for managing multiple Claude Code agents. Polls `agent@example.com` via IMAP, verifies that commands come exclusively from `user@example.com` (GPG signature or shared secret), executes them via `claude --print`, and replies via SMTP. Includes an MCP-based chat system where claude-email acts as the user's avatar, brokering conversations between the user (via email) and multiple Claude Code agents (via MCP tools).

- **Language / Runtime**: Python 3.12
- **Architecture**: Two user-level systemd services — claude-email (poller + user avatar) and claude-chat (MCP SSE server + SQLite message bus)
- **Test runner**: pytest (1675 tests: 1666 unit + 9 docker-gated e2e, 100% coverage on production code)

---

## Companion frontend

The user-facing frontend (aside from the direct email interface) lives in the **Claude-Email-App** project. Its agent on the chat bus is **`agent-Claude-Email-App`**. Any change that affects the frontend contract — envelope schema, routing semantics, MCP tool shape, dashboard feed, auth surface — must be coordinated with `agent-Claude-Email-App` via `chat_message_agent` before moving on. Don't land breaking changes here without an ack from that agent.

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

## Memory (mem0 via user-scope MCP)

Every email-driven invocation starts with `mcp__mem0__search_memory` scoped to `project="claude-email"`, `user_id="bb"`, and a one-line summary of the request. Fold relevant hits into the reply or plan.

Persist durable facts with `mcp__mem0__add_memory` at the same scope when the user asks to remember something, or when an incident, sender preference, or routing quirk surfaces. Skip storing anything already captured in code, git log, or this file.

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
├── tests/                 # 1666 unit tests (100% coverage)
│   └── e2e/               # 9 docker-gated end-to-end tests — real stack, zero mocks
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
- `tests/e2e/` is the only mock-free tree: every test there talks to a real mail server in
  docker over real sockets, and the `stack` fixture additionally boots the real
  `chat_server.py` and `main.py` as processes on throwaway ports with a throwaway GNUPGHOME.
  It carries the `e2e` marker (applied automatically by
  `tests/e2e/conftest.py`), so `-m "not e2e"` gives a docker-free run and `-m e2e` an
  opt-in one. Without docker it skips with a reason; it never fails. See `docs/e2e-testing.md`.
- `tests/conftest.py` is the **only** root conftest and holds **no fixtures** — it exists solely to unset inherited `GIT_*` redirection vars (`GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_COMMON_DIR`, `GIT_OBJECT_DIRECTORY`, `GIT_ALTERNATE_OBJECT_DIRECTORIES`, `GIT_PREFIX`). Git exports these into pre-commit hooks; in a linked worktree they point at the real repo and `GIT_DIR` beats `cwd=`, so any test shelling out to git would corrupt it. Shared fixtures still belong in underscore-prefixed helper modules. `tests/test_git_env_isolation.py` pins the guarantee.

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
- **100% coverage on production code** — `.coveragerc` omits `tests/`, the entry-shim, and standard pragma patterns; every merged change must keep the report at 100%
- **Docs follow code** — whenever a change alters user-visible behavior, configuration surface, or the test count, update `README.md` and the website (`website/index.html`, `website/fa/index.html` in lockstep) in the same PR

### Optional knobs
- `CLAUDE_EMAIL_EXCLUDE_DYNAMIC_PROMPT` — passes `--exclude-dynamic-system-prompt-sections` to the `claude` CLI to strip non-deterministic system-prompt sections (defaults to on; set to `0` to disable).
- `CLAUDE_EMAIL_MCP_NONBLOCKING` — sets `MCP_CONNECTION_NONBLOCKING=true` in the spawned CLI's env so slow/unhealthy MCP servers don't stall startup (defaults to off; set to `1` to enable).

---

## Operational notes

- **Restarting claude-chat severs every live MCP session.** The MCP SSE protocol has no re-handshake on the client side for this project's tools, so after `systemctl --user restart claude-chat` existing agents will return `-32602 Invalid request parameters` on their next `chat_register` / `chat_list_agents` call. That's not a parameter bug; it's the MCP router falling through to the user-scope mem0 server (which has no `chat_*` tools). Resolution: restart those Claude sessions, or wait for the startup proc-scan reconciliation to refresh their rows.
- **Blank radar after a restart is almost always stale PIDs**, not a bus outage. Compare `ps -ef | awk '$8=="claude"'` against the `agents` table before assuming the server is unhealthy.
- **Don't restart claude-chat to "fix" the blank radar** — each restart re-creates the problem. Touch the DB rows or rely on the proc-scan instead.
- **`CLAUDE_PROCESS_MARKER` defaults to `"claude"`, not `"bin/claude"`.** Interactive Claude CLIs have `claude …` in `/proc/<pid>/cmdline` with no path prefix; the stricter default stored ephemeral hook-helper PIDs for months and left live sessions invisible on the dashboard.

---

## Build Commands

```bash
.venv/bin/pytest tests/ -q            # Run all 1675 tests (e2e included)
.venv/bin/pytest tests/ -q -m "not e2e"  # Unit tests only — no docker needed
.venv/bin/pytest tests/ -q -m e2e     # End-to-end only — needs docker
.venv/bin/pytest tests/ -v            # Verbose
scripts/check-line-limit.sh           # Enforce 200-line file limit
```

---

## Starting a New Session

1. Read this file
2. Run `.venv/bin/pytest tests/ -q` — confirm 1675 tests pass (9 of them e2e, skipped without docker)
3. Invoke `superpowers:brainstorming` before any feature work
