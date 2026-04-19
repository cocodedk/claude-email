# Auto-Register Agents — Design Spec

**Date**: 2026-04-19
**Status**: Draft
**Author**: bb@cocode.dk + Claude

---

## 1. Problem

When a Claude Code session starts in a project whose `.mcp.json` declares the `claude-chat` MCP server, the chat tools (`chat_register`, `chat_ask`, `chat_notify`, `chat_check_messages`, `chat_deregister`, `chat_list_agents`) are *available*, but the agent does not register itself or adopt the chat-participant role.

This is the wrong default for this ecosystem. The user communicates with agents via email through the chat bus; an unregistered agent is invisible to that pipeline. Two observed failure modes:

1. Developer opens a session in `Dune-Browser-Game`, must explicitly say "please register" before any bus interaction works.
2. Spawned agents started by the email poller via `spawn_agent` inherit the same defect — registration relies on the caller remembering to prefix the `--print` instruction with "register first".

**Requirement**: agents MUST auto-register on session start, and they MUST know how to use the bus — in particular, that messages arriving on the bus are answered on the bus, not on stdout. (The user's email↔bus relay is one consumer of this; other agents sending `chat_ask` are another.)

## 2. Decision

**Deliver an instruction text via a `SessionStart` hook written into `.claude/settings.json`, sibling to `.mcp.json`, per project.**

The hook executes a shell command that prints a JSON payload to stdout with `hookSpecificOutput.additionalContext` containing the instruction. Claude Code feeds that text to the model as system-level guidance before the first turn.

**Empirically verified**: `claude --print "..."` fires `SessionStart` hooks (matcher `startup|resume`) — confirmed by writing a probe to `/tmp/hook-fired.log` from a throwaway project. So the hook covers both interactive sessions and spawner-launched `--print` sessions with a single mechanism.

### 2.1 Considered and rejected

- **Spawner prepends registration instruction to `--print`**. Covers spawned sessions only; interactive sessions the user opens themselves still need a separate mechanism. Belt-and-suspenders at best.
- **Per-project `CLAUDE.md` entry**. Pollutes project docs with ecosystem-specific instructions. `CLAUDE.md` is advisory; `SessionStart` hooks deliver deterministic, versioned context.
- **Server-side auto-register on SSE connect**. MCP SSE carries no cwd/project identity in the handshake, and adding one breaks the protocol. Requires protocol change and client cooperation — high cost.
- **`UserPromptSubmit` hook instead of `SessionStart`**. Fires on every user message, not just at session start. Redundant context injection and wrong semantics.

## 3. Instruction Text

The exact `additionalContext` string the hook emits. A single identity variable `caller` is defined once and reused verbatim — this avoids placeholder-substitution mistakes (codex flagged the earlier `agent-<name>` / `agent-<basename>` split invited `agent-agent-foo` errors).

```
You are a chat-connected agent on the claude-chat bus. Messages arriving
on the bus must be answered through bus tools, never through stdout.

Identity: your caller name is literally
  caller = "agent-" + basename(cwd)
Compute it once. Use the exact same string for every _caller= argument below.
Do not invent a new name later in the session.

1. Register (first chat-bus tool call of the session; idempotent — safe to
   repeat if you are unsure whether the spawner pre-registered you):
     mcp__claude-chat__chat_register(name=caller, project_path="<absolute cwd>")

2. Ask the user (blocks until the user replies or up to 1 hour, after which
   the tool returns {"error": ...}):
     mcp__claude-chat__chat_ask(_caller=caller, message="...")

3. One-way progress update (no reply expected):
     mcp__claude-chat__chat_notify(_caller=caller, message="...")

4. Inbox drain — consume-with-ack: messages returned are marked delivered
   and will NOT be seen again. Only call when you will stay alive long
   enough to act on what you receive. Never poll in a loop; never call as
   a "final drain" on the way out.
     mcp__claude-chat__chat_check_messages(_caller=caller)

5. Deregister on clean exit (best-effort; the server also reaps dead
   agents automatically, so this is a courtesy, not a guarantee):
     mcp__claude-chat__chat_deregister(_caller=caller)

Parameter reminders:
- chat_register uses name= and project_path=. Every other tool uses _caller=.
  The string value is the same in all of them — only the parameter name differs.
- Keep messages short — one paragraph. The user may be reading them in an
  email client; no raw logs, no long dumps.
```

### 3.1 Wording rationale

- **One symbolic `caller` variable, used literally** — codex flagged the earlier design's metavariable inconsistency as a likely model-error source.
- **`chat_register` called universally, with "idempotent" note** — removes the need for the instruction to know whether the session was spawned or manually opened. The server handles duplicates via `ON CONFLICT(name) DO UPDATE`.
- **`chat_ask` timeout stated literally** — matches `_ASK_TIMEOUT = 3600` in `chat/tools.py:22`. The earlier "blocks until reply" wording was a lie by omission.
- **`chat_check_messages` reframed as consume-with-ack** — `chat/tools.py:46-52` marks returned messages delivered immediately. The earlier "may drain on exit" advice would black-hole messages.
- **`chat_deregister` marked best-effort** — matches reality: `src/chat_db.py:93 reap_dead_agents` cleans up crashed sessions.
- **Stdout-vs-bus framing** — replaces the earlier "user reads via email" claim, which was false for manually opened interactive sessions. The invariant that's actually true: messages received on the bus should be answered on the bus.

## 4. Files & Components

### 4.1 `.claude/settings.json` (written per project)

```json
{
  "hooks": {
    "SessionStart": [{
      "matcher": "startup|resume",
      "hooks": [{
        "type": "command",
        "command": "<path-to-emit-script>"
      }]
    }]
  }
}
```

The `command` is a small script (not inline `echo`) so the JSON file stays readable and the instruction text can be updated in one place.

### 4.2 `scripts/chat-session-start-hook.sh` (new, in claude-email repo)

A POSIX shell script that prints the JSON Claude Code expects:

```sh
#!/bin/sh
# Emits hook output telling the session to behave as a chat-bus agent.
# Reads the instruction text from a sibling file so it stays under version control.
set -eu
instruction=$(cat "$(dirname "$0")/chat-agent-instruction.txt")
jq -nc --arg ctx "$instruction" \
  '{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $ctx}}'
```

Rationale for the separate `.txt`: the instruction is long-form natural language; embedding it in JSON-in-shell-in-JSON is fragile. `jq -n` handles JSON escaping safely.

### 4.3 `scripts/chat-agent-instruction.txt` (new)

Literal copy of section 3's instruction text.

### 4.4 `src/spawner.py` changes

`inject_mcp_config` becomes `inject_chat_bootstrap` and writes both:

- `.mcp.json` (existing behavior)
- `.claude/settings.json` with the hook pointing at an absolute path to `scripts/chat-session-start-hook.sh` inside the claude-email repo

The hook script path is resolved at spawn time from the claude-email install dir (discoverable via `__file__`). The emitted settings file is idempotent on re-spawn (read-merge-write).

### 4.5 Migration for existing chat-enabled projects

Two projects already have `.mcp.json` but no hook: `claude-email` (this repo) and `Dune-Browser-Game`. The spawner's new `inject_chat_bootstrap` is idempotent, so the migration is a single call to that function per project, run from a short ad-hoc Python one-liner after merge. No new CLI subcommand. Not a long-term concern — all future `spawn_agent` calls write both files automatically.

## 5. Testing

Per project CLAUDE.md: TDD, 100% coverage on production code, files ≤200 lines.

### 5.1 Unit tests (`tests/test_spawner.py` — extend)

- `test_inject_chat_bootstrap_writes_both_files`: after call, both `.mcp.json` and `.claude/settings.json` exist with expected structure.
- `test_inject_chat_bootstrap_merges_existing_settings`: pre-existing `.claude/settings.json` with unrelated keys is preserved; only `hooks.SessionStart` is added/replaced.
- `test_inject_chat_bootstrap_idempotent`: calling twice yields the same file contents.
- `test_hook_command_path_is_absolute`: the path written into `settings.json` is absolute, not relative, because Claude Code resolves hook commands from the *session* cwd, not the repo.

### 5.2 Hook script test (`tests/test_hook_script.py` — new)

Runs the shell script as a subprocess, asserts valid JSON on stdout, asserts `additionalContext` contains the key phrases `chat_register`, `_caller`, `chat_ask`.

### 5.3 End-to-end (manual, documented)

1. Spawn a throwaway agent in `/tmp/e2e-reg`.
2. Immediately query `chat_list_agents` — confirm `agent-e2e-reg` appears with status `running` within 5 seconds of spawn.
3. Send it a `chat_ask` from the bus, observe it responds without being told to register first.

## 6. Rollout

1. Add `scripts/chat-session-start-hook.sh` and `scripts/chat-agent-instruction.txt` (committed, executable).
2. Update `src/spawner.py` + tests. Keep all 244 existing tests green; add the new ones.
3. `scripts/check-line-limit.sh` must pass.
4. Manually write `.claude/settings.json` into the two existing chat-enabled projects (`claude-email`, `Dune-Browser-Game`) so their already-running sessions auto-register on next restart.
5. `simplify` review pass.
6. `superpowers:requesting-code-review` before merge.
7. Restart `claude-email` service (with user confirmation — there may be running spawned agents).
8. Update `README.md` and `website/index.html` + `website/fa/index.html` in the same PR (per project CLAUDE.md rule: docs follow code).

## 7. Risks & Non-Goals

### Risks

- **Identity collision** between two sessions in the same repo. `db.register_agent` (`src/chat_db.py:56`) is `ON CONFLICT(name) DO UPDATE` — the second session's `project_path` and `last_seen_at` overwrite the first's, so two `agent-<basename>` sessions stomp on each other on the bus. Acceptable for this iteration; the identity redesign in section 8 addresses it properly.
- **Hook file not executable** → hook silently fails, agent starts without instruction. Mitigation: `chmod +x` on the script at install, and a spawner assertion that checks the mode before writing the settings file.
- **Path drift** if the claude-email repo is moved after projects are bootstrapped. The hook command is an absolute path; moving the repo breaks existing projects' hooks. Mitigation: document the dependency in `README.md`; re-running `inject_chat_bootstrap` per project fixes it.
- **Instruction drift** — if we edit the `.txt` but forget to re-deploy. Acceptable: the script reads the file at session start, so updating the `.txt` propagates immediately to all projects whose hook points at this install.
- **Prompt compliance ≠ determinism**. The hook gives us reliable *delivery* of guidance; the model still has to follow it. Per codex, this is "better compliance, not deterministic behavior" — acceptable for now because the server-side fallback (spawner pre-registers; stale agents get reaped) catches the common failure modes.

### Non-goals

- No enforcement of `chat_deregister` on exit. The server already marks stale agents `disconnected` after a timeout; forgetting is harmless.
- No automatic cleanup of the written `.claude/settings.json` in target projects. The hook is inert outside the claude-chat ecosystem (it only injects context; no side effects if the MCP server is down).

## 8. Future Work (not this iteration)

Codex recommended moving caller identity below the prompt layer to make registration truly deterministic. That's the correct long-term direction, deferred because it carries schema and tool-API changes. Candidate options, in increasing order of invasiveness:

1. **Separate `agent_id` from `display_name`.** Spawner generates a UUID-based `agent_id`, stores it in an env var (`CLAUDE_CHAT_AGENT_ID`) or a per-session file, and the hook injects it as the `caller` value. `display_name` stays human-readable; collisions on display are permitted.
2. **Bootstrap tool `chat_whoami()`** — no args, returns the caller handle based on something the server can derive (SSE session id correlated with a token the spawner writes). Model calls it once and reuses the result. No model-side name derivation.
3. **Lazy server-side registration on first tool use.** First tool call with an unknown `_caller` auto-creates the agent row from a session token. Requires protocol extension.

All three eliminate the "hope the model computes the right name" failure mode. Pick one in a follow-up spec.
