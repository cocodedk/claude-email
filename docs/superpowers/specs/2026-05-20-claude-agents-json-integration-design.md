# Design: `claude agents --json` Integration

**Branch:** `claude/task-65-claude-agents-json-integration`
**Date:** 2026-05-20

---

## Problem

Two bugs documented in CLAUDE.md stem from the same root cause — the PPID chain walk in `process_liveness.find_ancestor_pid_matching()`:

1. **Blank radar after restart** — stale PIDs survive in the DB because the walk stored a short-lived hook-helper PID instead of the long-lived Claude session PID.
2. **Silent-drain-skip** — `chat_pid_reclaim.py` walks the PPID chain looking for a process whose `argv[0]` basename matches `CLAUDE_PROCESS_MARKER`; if a shell wrapper intercepts the walk before reaching the Claude process, reclaim silently no-ops and the slot stays broken.

`claude agents --json` emits a structured JSON array of live sessions with `pid`, `cwd`, `sessionId`, and `status`. Matching on `cwd` is authoritative — no heuristics.

---

## JSON Schema (observed, 2026-05-20)

```json
[
  {
    "pid": 15332,
    "cwd": "/home/cocodedk/0-projects/claude-email",
    "kind": "interactive",
    "startedAt": 1779261911533,
    "sessionId": "1f48b55f-ef46-4f89-8e25-a144176f93a3",
    "name": "CLAUDE-EMAIL",
    "status": "busy"
  }
]
```

Fields used: `pid`, `cwd`. `sessionId` stored for future observability.

---

## Feature 1 — `claude agents --json` proc-scan replacement

### `src/process_liveness.py` — new function

```python
def find_session_pid_for_cwd(
    cwd: str,
    claude_bin: str = "claude",
) -> int | None:
```

- Runs `[claude_bin, "agents", "--json"]` via `subprocess.run(shell=False, timeout=5)`.
- Parses the JSON array; returns the `pid` of the entry whose `cwd` matches the resolved `cwd` argument.
- Multiple matches (two sessions in the same dir): return the one with the highest `startedAt` (most recent).
- Any failure (subprocess error, JSON decode error, no match): return `None`.
- Does **not** raise — callers treat `None` as "fall back to PPID walk".

### `src/chat_pid_reclaim.py` — updated reclaim order

Replace the single `find_ancestor_pid_matching(_CLAUDE_CMDLINE_MARKER)` call with:

```python
claude_pid = find_session_pid_for_cwd(cwd, claude_bin=_CLAUDE_BIN)
if claude_pid is None:
    claude_pid = find_ancestor_pid_matching(_CLAUDE_CMDLINE_MARKER)
```

`_CLAUDE_BIN` reads from `CLAUDE_BIN` env var, defaulting to `"claude"` (same pattern as `CLAUDE_PROCESS_MARKER`).

### Invariants preserved

- `shell=False` — no command injection risk.
- No new imports in `process_liveness.py` beyond `subprocess` (already used by the project).
- PPID walk stays as fallback — no regression in CI or restricted environments.

---

## Feature 2 — Stop hook `background_tasks` / `session_crons` awareness

### Context

`scripts/chat-drain-inbox.py` is already wired as the Stop hook handler via `agent_bootstrap.inject_session_start_hook`. It currently emits `{"decision": "block", "reason": ...}` to cancel the stop when unread messages are waiting.

The new `background_tasks` and `session_crons` fields in the Stop payload tell us whether the stopping session had unfinished scheduled work.

### Change: log a flow event when stopping with pending work

In `scripts/chat-precompact-hook.py`-style fashion, a **new script** `scripts/chat-stop-hook.py`:

- Reads the Stop hook payload from stdin.
- Skips if `agent_id` is set (subagent — master owns the bus slot).
- If `background_tasks` is non-empty or `session_crons` is non-empty, calls `db._log_event(caller, "hook_stop_pending_work", summary)` where summary lists counts.
- Always exits 0 (best-effort telemetry, never blocks the stop).

`agent_bootstrap.inject_session_start_hook` gains an optional `stop_hook_script_path` parameter (defaults to `STOP_HOOK_SCRIPT`). The Stop hook event gets **both** `drain_script_path` and `stop_hook_script_path` in its command list (drain first, stop-hook second).

---

## Files changed

| File | Change |
|------|--------|
| `src/process_liveness.py` | Add `find_session_pid_for_cwd()` |
| `src/chat_pid_reclaim.py` | Use `find_session_pid_for_cwd` as primary, PPID walk as fallback |
| `src/agent_bootstrap.py` | Add `STOP_HOOK_SCRIPT`; wire it into Stop hook alongside drain |
| `scripts/chat-stop-hook.py` | New script — log flow event on stop with pending work |
| `tests/test_process_liveness_*.py` | Tests for `find_session_pid_for_cwd` |
| `tests/test_chat_pid_reclaim_*.py` | Tests for updated reclaim order |
| `tests/test_chat_stop_hook_*.py` | Tests for new stop hook script |
| `tests/test_agent_bootstrap_stop_hook*.py` | Tests for updated bootstrap wiring |

---

## Testing strategy

- `find_session_pid_for_cwd`: mock `subprocess.run` — test match, no-match, multi-match (pick highest `startedAt`), subprocess failure, JSON decode error.
- `chat_pid_reclaim`: test that JSON path is tried first; test fallback fires when JSON returns `None`; existing tests must still pass unchanged.
- `chat-stop-hook.py`: test skip on `agent_id`, test log event emitted with correct counts, test fail-open on DB error.
- `agent_bootstrap`: test that Stop hook command list includes both drain and stop-hook scripts; existing hook wiring tests still pass.

---

## Out of scope

- Storing `sessionId` in the DB (useful future work, not needed now).
- Replacing `is_alive()` with a `claude agents --json` poll (too slow for the hot path; `os.kill(pid, 0)` stays).
- Any change to the dashboard queries or envelope schema.
