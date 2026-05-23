# Claude CLI Invocation Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align all `claude` CLI subprocess invocations with the patterns Anthropic now recommends in claude-code 2.1.150 — explicit `stdin=DEVNULL` everywhere, config-gated default-on `--exclude-dynamic-system-prompt-sections` for cache reuse on the email router, and an opt-in `MCP_CONNECTION_NONBLOCKING=true` env knob for `--print` calls that pass `--mcp-config`.

**Architecture:** Default behavior changes are limited and reversible. Each spawn site gets `stdin=subprocess.DEVNULL` so a hostile parent (systemd unit, future asyncio context) can never leak open stdin into `claude` and cause the CLI to block reading from it. Two new `config.py` knobs (`claude_exclude_dynamic_prompt`, default on; `claude_mcp_nonblocking`, default off) plumb the new flag and env var into `executor.py` and `project_worker.py` only — sites that talk to the email router and the per-project task worker. `spawner.py` keeps its "spawn long-running interactive `claude` without `--print`" pattern (deliberate; the SessionStart hook registers the agent and the chat MCP keeps it conversant) but gains `stdin=DEVNULL`.

**Rollback:** If any task fails after code changes, stop before later tasks because later signatures and assertions assume earlier tasks landed. Roll back only the files named in that task's **Files** list, then rerun that task's focused failing test. Runtime rollback without code changes is available for the new behavior: set `CLAUDE_EMAIL_EXCLUDE_DYNAMIC_PROMPT=0` to suppress the prompt-cache flag, and leave `CLAUDE_EMAIL_MCP_NONBLOCKING` unset or non-`1` to keep MCP connection waiting at the claude-code default.

**Tech Stack:** Python 3.12 · `subprocess` (shell=False) · `asyncio.subprocess` · pytest+mocker · existing `_executor_helpers.py` / `_project_worker_helpers.py` fixtures.

---

## Contents

- [File map](file-structure.md) — every source/test file this plan touches and what each is responsible for.
- [Task index](task-index.md) — one-line summary of each task.
- [Background](background.md) — why each of the three CLI changes lands now and what claude-code release enabled it.

### Tasks (execute in order)

1. [Task 1 — `executor.py` `stdin=DEVNULL`](tasks/01-executor-stdin.md)
2. [Task 2 — `spawner.py` `stdin=DEVNULL`](tasks/02-spawner-stdin.md)
3. [Task 3 — `project_worker.py` `stdin=DEVNULL`](tasks/03-project-worker-stdin.md)
4. [Task 4 — `process_liveness.py` `stdin=DEVNULL`](tasks/04-process-liveness-stdin.md)
5. [Task 5 — `--exclude-dynamic-system-prompt-sections` (config-gated, default on)](tasks/05-exclude-dynamic-prompt-flag.md)
6. [Task 6 — `MCP_CONNECTION_NONBLOCKING=true` (opt-in env)](tasks/06-mcp-nonblocking-env.md)
7. [Task 7 — Verification](tasks/07-verification.md)
