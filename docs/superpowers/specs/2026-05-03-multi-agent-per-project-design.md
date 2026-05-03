# Multi-Agent Per Project — Design

**Date:** 2026-05-03
**Status:** Draft (awaiting user review)

## Goal

Allow N concurrent Claude Code sessions to run in the same project directory and exchange messages reliably on the claude-chat bus. Each session must have its own bus identity (agent name) and a live PID attribution so message delivery doesn't fall through to wake-spawn or get marked failed.

## Background

Today's bus assumes one agent per project directory. Three coupled facts cause a second agent in the same cwd to lose message delivery:

1. **`scripts/chat-register-self.py:93`** derives the agent name as `agent-<basename(cwd)>`. Two sessions in the same cwd compute the same name. The second one is silenced by `_master_already_owns` (line 111-114).
2. **`chat/tools.py:18`** (`chat_register` MCP tool, the model-side fallback) calls `db.register_agent(name, project_path)` with no PID — MCP/SSE doesn't expose the caller's PID.
3. **`src/agent_registry.py:72-81`** raises `AgentProjectTaken` when a second live PID tries to register against an already-occupied `project_path`.

Net effect: the second agent ends up with `pid=NULL`. The wake-watcher (`src/wake_helpers.py:23`) treats that as dormant, tries to spawn a transient session to drain the inbox, fails repeatedly, and after escalation marks pending messages `status='failed'` (`src/wake_watcher.py:137`).

A separate path — **`src/proc_reconcile.py:84`** — backfills PIDs after a bus restart, but it also derives the name as `agent-<basename(cwd)>`, so it can only ever attribute a PID to the first agent in a cwd.

## Non-goals

- Decoupling delivery liveness from PID (replacing `_has_live_owner` with a `last_seen_at` freshness check). This is a larger refactor that touches wake-watcher semantics, dashboard radar UX, and the wake_session_store. Defer to a follow-up.
- Changing the MCP `chat_register` tool surface. The frontend (`agent-Claude-Email-App`) consumes the bus contract; keeping the tool shape stable means no coordination cost for this change.
- DB schema migration. The `agents` table is `name TEXT PRIMARY KEY`, no uniqueness on `project_path`. The "one per project" rule is purely application-layer.

## Mechanism

A single new environment variable, `CLAUDE_AGENT_NAME`, threads through three sites that today derive a name from cwd. When set, it overrides the default; when unset, current single-tenant behavior is preserved (full backward compatibility).

### Name format

```
^agent-[a-z0-9][a-z0-9_-]{0,57}$
```

Lowercase ASCII, must start with `agent-` followed by alphanumeric, then up to 57 hyphen/underscore/alphanumeric chars (max 64 total: `agent-` is 6 chars + 1 anchor + up to 57). Validated at every read site; invalid input falls back to the cwd-derived default with a stderr warning. Centralized in a small helper (e.g., `src/agent_name.py::validated_agent_name(raw, fallback) -> str`) to keep the rule in one place.

## Touch points

| File | Change |
|---|---|
| `src/agent_name.py` (new) | Helper: `validated_agent_name(raw: str \| None, fallback: str) -> str`. Returns validated `raw` if set and matches the regex, else `fallback`. Logs a stderr warning when a non-empty `raw` fails validation. |
| `scripts/chat-register-self.py:93` | Read `os.environ.get("CLAUDE_AGENT_NAME")` through `validated_agent_name(...)` before falling back to `agent-<basename(cwd)>`. PID computation (`_durable_session_pid`) unchanged — already correct. |
| `src/spawner.py:60` | Add optional kwarg `agent_name: str \| None = None`. When set: validate, use it as the name (replacing `build_agent_name(project_dir)`), inject `CLAUDE_AGENT_NAME` into the spawned process env (extending `extra_env`). Collision guard at `:88-94` continues to apply against the chosen name. |
| `src/proc_reconcile.py:84` | Before computing `agent-<basename(cwd)>`, attempt to read `/proc/<pid>/environ`, parse `CLAUDE_AGENT_NAME=`, and pass through `validated_agent_name`. The existing `AgentNameTaken/AgentProjectTaken` retry loop stays as a backstop for unflagged sessions. |
| `src/agent_registry.py:72-81` | Remove the `AgentProjectTaken` raise (and the conflict-scan that drives it). Keep `AgentNameTaken` — still enforced via the `name` PRIMARY KEY and the live-pid check at lines 64-71. |
| `src/chat_errors.py` | Remove `AgentProjectTaken` if no longer imported anywhere after the registry change (verify with `grep -rn AgentProjectTaken src/ chat/ tests/`). Otherwise keep the symbol but unused, with a docstring noting it's deprecated. |
| `src/chat_handlers.py:139-148` (`spawn` meta-command dispatch) | Extend syntax: `spawn <path>` (unchanged), `spawn <path> [instruction]` (unchanged), `spawn <path> as <agent-name> [instruction]` (new — when token[1] == `as`, consume token[2] as the agent name). Validate via `validated_agent_name`, pass to `spawner.spawn_agent(agent_name=...)`. Reject invalid names with a clear `Error` reply. |
| `chat/tools.py:18` (`chat_register` MCP tool) | **No change.** Hook is canonical; MCP path remains a no-PID fallback. Update the inline docstring to note this. |

## Data flow (after change)

**Spawn flow (email command `spawn <path> as agent-foo`):**

1. Email parser extracts path + name → calls `spawn_agent(project_dir, agent_name="agent-foo", ...)`.
2. Spawner validates name, sets `extra_env["CLAUDE_AGENT_NAME"] = "agent-foo"`, launches `claude` with that env.
3. Inside the spawned session, the SessionStart hook reads `$CLAUDE_AGENT_NAME`, registers `agent-foo` with the durable Claude PID. Row has correct name + PID from the start.

**Manual second-agent flow (user runs `CLAUDE_AGENT_NAME=agent-foo claude` in an existing project dir):**

1. Hook reads the env var, registers `agent-foo` with that session's PID. The first agent's row (`agent-<basename>`) is untouched because the names differ. Both rows now coexist.

**Bus restart reconcile:**

1. `proc_reconcile.reconcile_live_agents` iterates live `claude` PIDs.
2. For each PID, reads `/proc/<pid>/environ` first, parses `CLAUDE_AGENT_NAME`. If set/valid → use it. If absent → fall back to `agent-<basename(cwd)>`.
3. Each PID gets attributed to its correct name. N agents per cwd, no collisions.

**Message delivery (after either flow):**

- Sender → recipient row has correct PID → `_has_live_owner` returns True → wake-watcher skips → live agent's own Stop/UserPromptSubmit hook drains the inbox on next turn. No more `status='failed'` from spawn loops.

## Failure modes

| Scenario | Behavior |
|---|---|
| Env var set to invalid string | `validated_agent_name` returns fallback; stderr warning; system stays usable. |
| Env var set, name already held by a live process | `AgentNameTaken`; hook silently concedes via existing `_master_already_owns` path. |
| Two same-cwd processes, both env-tagged distinctly | Reconcile picks correct name for each via `/proc/<pid>/environ`. Both visible. |
| Two same-cwd processes, one env-tagged, one not | Tagged one keeps its name; untagged one falls back to `agent-<basename>`. If that basename name is already held by the tagged one, the untagged one collides → reconcile retries with `agent-<parent>-<basename>` per existing fallback logic. |
| Spawn requested with name that collides with another project's existing agent | Existing collision guard in `spawner.py:88-94` raises `ValueError`. Behavior preserved. |

## Testing

**New tests:**

- `validated_agent_name`: valid pass-through, invalid → fallback, empty → fallback, all length/charset edge cases.
- Hook (`chat-register-self`): `CLAUDE_AGENT_NAME` honored when set; cwd fallback when unset; invalid env → fallback + stderr warning; pre-existing live owner with different name doesn't block new registration.
- Spawner: `agent_name` kwarg honored; env var injected into child process env; collision guard applies to explicit name; default behavior preserved when kwarg omitted.
- proc_reconcile: PID with `CLAUDE_AGENT_NAME` in environ resolves to that name; PID without falls back to basename; mixed scenario in same cwd resolves both.
- Email command parsing: `spawn <path>` unchanged; `spawn <path> as <name>` parsed; invalid name rejected with a clear error reply.
- Registry: two live registrations against the same `project_path` (with different names) both succeed; same name + different live PIDs still raises `AgentNameTaken`.

**Existing tests to flip:**

- Any test asserting `AgentProjectTaken` on second-register-into-same-project_path → flip to assert success (or delete if covered elsewhere).

**Coverage:** stays at 100% on production code (excluding tests/ and standard pragma patterns). Total test count grows from 1108.

## Rollout

- Single PR.
- Run `.venv/bin/pytest tests/ -q` — must pass with the new total.
- `scripts/check-line-limit.sh` — must pass.
- README + website (`website/index.html`, `website/fa/index.html` in lockstep) updated for the new `spawn ... as <name>` syntax.
- Heads-up to `agent-Claude-Email-App` via `chat_message_agent` per CLAUDE.md — bus contract is unchanged, but the dashboard may now show multiple rows for the same `project_path`. They should verify rendering doesn't assume uniqueness.

## Open questions

None. Defaults committed. Implementation can proceed once user reviews this spec.
