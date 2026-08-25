# CLAUDE.md — claude-email

## Project Overview

Email-driven wrapper for the Claude Code CLI with an integrated chat relay for managing multiple Claude Code agents. Polls `agent@example.com` via IMAP, verifies that commands come exclusively from `user@example.com` (GPG signature or shared secret), executes them via `claude --print`, and replies via SMTP. Includes an MCP-based chat system where claude-email acts as the user's avatar, brokering conversations between the user (via email) and multiple Claude Code agents (via MCP tools).

- **Language / Runtime**: Python 3.12
- **Architecture**: Two user-level systemd services — claude-email (poller + user avatar) and claude-chat (MCP SSE server + SQLite message bus)
- **Test runner**: pytest (1792 tests: 1713 unit + 79 docker-gated e2e, 100% coverage on production code)

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
│   ├── secret_redact.py   # Outbound scrub: the shared secret never leaves in a body or a header
│   ├── replay_guard.py    # Content-bound replay key (digest of the OpenPGP signature)
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
├── tests/                 # 1713 unit tests (100% coverage)
│   └── e2e/               # 79 docker-gated end-to-end tests — real stack, zero mocks
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
- `processed_ids.json` is the idempotency store — never delete it in production.
  It holds two kinds of key: `Message-ID`s (idempotent redelivery) and
  `sig:<sha256>` content-bound replay keys from `src/replay_guard.py` (a
  captured *signed* credential is single-use). The Message-ID alone is not
  replay protection — no credential this system accepts covers that header.
- `claude-chat.db` is the shared SQLite database (WAL mode) — used by both services
- `tests/e2e/` is the only mock-free tree: every test there talks to a real mail server in
  docker over real sockets, and the `stack` fixture additionally boots the real
  `chat_server.py` and `main.py` as processes on throwaway ports with a throwaway GNUPGHOME.
  `tests/e2e/test_happy_path.py` drives one real command through that stack and
  asserts only on outside observables (reply mail, receipt file, DB rows); it
  swaps the harness's refusing `CLAUDE_BIN` stub for a deterministic executable
  and restores it on teardown — the CLI is outside the SUT, everything else is real.
  `tests/e2e/test_auth_matrix.py` asserts the full authentication grid — six
  inbound routes against five conditions (unsigned, wrong key, stale timestamp,
  replayed nonce, valid) — and boots a second poller with `SHARED_SECRET=""` for
  the GPG-only deployment. See `docs/e2e-auth-matrix.md`; it records that **no
  route enforces a timestamp freshness window**, so the poller's idempotency
  store is the only temporal control — by Message-ID on the unsigned bearer
  routes, and additionally by content-bound replay key wherever a GPG signature
  is present — and that the JSON envelope path now requires `SHARED_SECRET`
  (it fails closed when unset).
  `tests/e2e/test_replay.py` replays one real captured signed command —
  byte-identical, then under a FRESH `Message-ID` with a bumped `Date` — and
  asserts the *effect* happened exactly once (one CLI execution in an
  append-only ledger, one `[Result]`, one bus row), with an out-of-band
  `gpg --verify` proving the replay was still authentic and a later tracer
  proving the poller was awake. See `docs/e2e-replay.md`.
  `tests/e2e/test_metamorphic_headers.py` is the metamorphic property: one
  captured signed payload re-sent under mutated `Subject` / `In-Reply-To` /
  `References` / `To`, asserting the executed command, the routing target and
  the reconstructed prompt never move. It takes the **rejection** branch of
  that disjunction — the router still reads those headers and would still act
  on them; nothing reaches it holding a spent credential, because
  `fetch_unseen` refuses the replayed signature first. Any future inbound path
  that reaches routing without passing `fetch_unseen` reopens the exposure.
  See `docs/e2e-metamorphic-headers.md`.
  `tests/e2e/test_invariants.py` asserts three properties over a generated
  stream of real messages, on a **second real poller booted with
  `GPG_FINGERPRINT=""`** (the bearer-token deployment — `is_authorized`
  returns on the GPG branch whenever a fingerprint is set, so the
  shared-secret routes are unreachable on the session stack): the secret
  appears in no outbound body **and no outbound header**, every accepted
  inbound message has exactly one ledger row, and no effect is observed
  twice. The header half is the point — the leak was in `Subject`, which
  `send_threaded_reply` echoes from the inbound mail. Fixed by
  `src/secret_redact.py`, applied at the `src/mailer.send_reply` choke point
  so all three callers are covered. Note the stream's duplicates are
  **byte-identical redeliveries**: a bearer message under a fresh
  `Message-ID` executes again by design, since no credential on that route
  covers any header. See `docs/e2e-invariants.md`.
  `tests/e2e/test_failure_injection.py` breaks the real dependencies
  mid-flight — `docker compose kill` on a private GreenMail container, a
  SIGKILL on a real `src.project_worker` and its CLI child, a TCP severance
  of the live IMAP session at the instant the poller issues `FETCH`, and a
  SIGKILL on `main.py` inside the accept→execute window — and asserts the
  documented outcome of each. It pins the delivery guarantee as
  **at most once**: `UID FETCH (RFC822)` sets `\Seen` server-side (asserted
  against the live server, not quoted from the RFC) and `mark_processed` runs
  only after dispatch, so a crash can never duplicate an effect and a crash
  after the fetch drops the command outright. The drop leaves three durable
  traces — the message still in the mailbox and `\Seen`, its `Message-ID`
  absent from `STATE_FILE`, no ledger entry — but the user is **not** notified,
  because the `[Running]` ack dies on the wire with the process. It boots its
  own mail server (own compose project, container and ports) since killing a
  GreenMail destroys every mailbox on it. See `docs/e2e-failure-injection.md`.
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
.venv/bin/pytest tests/ -q            # Run all 1792 tests (e2e included)
.venv/bin/pytest tests/ -q -m "not e2e"  # Unit tests only — no docker needed
.venv/bin/pytest tests/ -q -m e2e     # End-to-end only — needs docker
.venv/bin/pytest tests/ -v            # Verbose
scripts/check-line-limit.sh           # Enforce 200-line file limit
```

---

## Starting a New Session

1. Read this file
2. Run `.venv/bin/pytest tests/ -q` — confirm 1792 tests pass (79 of them e2e, skipped without docker)
3. Invoke `superpowers:brainstorming` before any feature work
