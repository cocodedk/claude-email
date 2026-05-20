# claude agents --json Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the fragile `/proc`-based PPID chain walk with `claude agents --json` as the primary PID-lookup strategy, and add a Stop hook that logs pending work on session exit.

**Architecture:** `find_session_pid_for_cwd()` is added to `process_liveness.py`; `chat_pid_reclaim.py` calls it first (only when stored PID is stale) and falls back to `find_ancestor_pid_matching` on None. A new `scripts/chat-stop-hook.py` logs a flow event when stopping with background tasks or crons pending; `agent_bootstrap.py` wires it into the Stop hook event alongside the existing drain script.

**Tech Stack:** Python 3.12, subprocess (shell=False), json, pytest monkeypatch

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/hook_utils.py` | Create | Shared hook helpers: `resolved_db_path`, `caller_name`, `read_hook_payload` |
| `src/process_liveness.py` | Modify | Add `find_session_pid_for_cwd()` |
| `src/chat_pid_reclaim.py` | Modify | Use new function when stored PID is stale; PPID walk as fallback |
| `src/agent_bootstrap.py` | Modify | Add `_resolve_script()` helper; `STOP_HOOK_SCRIPT`; wire into Stop event |
| `scripts/chat-precompact-hook.py` | Modify | Import shared helpers from `src/hook_utils` |
| `scripts/chat-stop-hook.py` | Create | Read Stop payload; log flow event on pending work |
| `tests/test_hook_utils.py` | Create | Tests for `src/hook_utils` shared helpers |
| `tests/test_process_liveness_session_pid.py` | Create | Tests for `find_session_pid_for_cwd` |
| `tests/test_chat_pid_reclaim_session_pid.py` | Create | Tests for updated reclaim order (new primary path) |
| `tests/test_chat_drain_inbox_pid_reclaim_noops.py` | Modify | Mock new function so existing tests still test PPID-walk fallback |
| `tests/test_chat_drain_inbox_pid_reclaim_drain.py` | Modify | Same — mock new function to return None |
| `tests/test_chat_drain_inbox_pid_reclaim_force.py` | Modify | Same |
| `tests/test_spawner_session_hook.py` | Modify | Assert Stop hook now includes both drain + stop-hook scripts |
| `tests/test_chat_stop_hook_skip.py` | Create | Subagent skip, stdin edge cases |
| `tests/test_chat_stop_hook_emission.py` | Create | Flow event emitted for background_tasks / session_crons |
| `tests/test_chat_stop_hook_fail_open.py` | Create | DB errors exit 0 |

---

## Task Index

| Task | File | Description |
|------|------|-------------|
| Task 0 | [task-0-hook-utils.md](2026-05-20-claude-agents-json-integration/task-0-hook-utils.md) | Extract `src/hook_utils.py` — shared hook helpers |
| Task 1 | [task-1-find-session-pid.md](2026-05-20-claude-agents-json-integration/task-1-find-session-pid.md) | Add `find_session_pid_for_cwd` to `process_liveness.py` |
| Task 2a | [task-2a-pid-reclaim-tests.md](2026-05-20-claude-agents-json-integration/task-2a-pid-reclaim-tests.md) | Write failing tests for updated `chat_pid_reclaim.py` |
| Task 2b | [task-2b-pid-reclaim-impl.md](2026-05-20-claude-agents-json-integration/task-2b-pid-reclaim-impl.md) | Implement reclaim changes + patch existing tests |
| Task 3a | [task-3a-stop-hook-tests.md](2026-05-20-claude-agents-json-integration/task-3a-stop-hook-tests.md) | Write failing tests for `chat-stop-hook.py` |
| Task 3b | [task-3b-stop-hook-impl.md](2026-05-20-claude-agents-json-integration/task-3b-stop-hook-impl.md) | Implement `chat-stop-hook.py` |
| Task 4 | [task-4-agent-bootstrap.md](2026-05-20-claude-agents-json-integration/task-4-agent-bootstrap.md) | Wire stop hook into `agent_bootstrap.py` |
| Task 5 | [task-5-verification.md](2026-05-20-claude-agents-json-integration/task-5-verification.md) | Line-limit check + final verification |
