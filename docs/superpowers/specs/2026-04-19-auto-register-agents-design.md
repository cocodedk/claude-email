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

**Requirement**: agents MUST auto-register on session start, and they MUST know how to use the bus to communicate with the user (who reads messages as email, not stdout).

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

The exact `additionalContext` string the hook emits (literal — no substitutions beyond the agent name derivation):

```
You are a chat-connected agent on the claude-chat bus. The user reads your
messages via email — NOT stdout. Do not ask the user questions in stdout;
they are not watching.

Derive your agent name: name = "agent-" + basename(cwd).

First action, before any other tool call:
  mcp__claude-chat__chat_register(name="<agent-name>", project_path="<absolute cwd>")

To ask the user (blocks until they reply):
  mcp__claude-chat__chat_ask(_caller="<agent-name>", message="...")

To send a one-way progress update (no reply expected):
  mcp__claude-chat__chat_notify(_caller="<agent-name>", message="...")

To drain the inbox (do this when blocked, idle, or about to exit):
  mcp__claude-chat__chat_check_messages(_caller="<agent-name>")

Before exit:
  mcp__claude-chat__chat_deregister(_caller="<agent-name>")

Notes:
- chat_register takes name= and project_path=. Every other tool takes _caller=
  with that same name string. Do not mix them up.
- Keep messages short — one paragraph each. No raw logs or long dumps; the
  user reads them in an email client.
- If you are running in --print mode (one-shot), you may still call
  chat_check_messages once before exit to drain any pending reply from a
  concurrent chat_ask; otherwise the main polling loop does not apply.
```

### 3.1 Wording rationale (addresses advisor + implementation risk)

- `_caller` vs `name`/`project_path` distinction is called out explicitly — mid-capability models conflate these.
- `--print` behavior is addressed inline rather than branching to a separate instruction variant.
- Message-length guidance prevents agents from dumping raw tool output to the user's inbox.
- Stdout-is-not-watched is stated twice (header + step 2) because it is the top failure mode.

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

- **Hook file not executable** → hook silently fails, agent starts without instruction. Mitigation: `chmod +x` in install, and a spawner assertion.
- **Path drift** if the claude-email repo is moved after projects are bootstrapped. The hook command is an absolute path; moving the repo breaks existing projects' hooks. Mitigation: document the dependency in `README.md`; re-running `inject_chat_bootstrap` per project fixes it.
- **Instruction drift** — if we edit the `.txt` but forget to re-deploy. Acceptable: the script reads the file at session start, so updating the `.txt` propagates immediately to all projects whose hook points at this install.

### Non-goals

- No enforcement of `chat_deregister` on exit. The server already marks stale agents `disconnected` after a timeout; forgetting is harmless.
- No multi-agent-per-directory support. Name collision (`agent-<basename>`) is accepted — `spawn_agent` already enforces this convention.
- No automatic cleanup of the written `.claude/settings.json` in target projects. The hook is inert outside the claude-chat ecosystem (it only injects context; no side effects if the MCP server is down).
