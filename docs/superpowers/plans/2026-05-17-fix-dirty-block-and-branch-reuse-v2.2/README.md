# Fix Dirty-Repo Blocking & Branch Reuse — Plan v2

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Each phase file lists tasks as `- [ ]` checkboxes; execute in numbered order across phase files. Phases A→H map to files `phase-a-*.md` … `phase-h-*.md` in this folder.

**Version:** v2 (timestamp 2026-05-17), revision 2 folded in same date
**Supersedes:** `../2026-05-17-fix-dirty-block-and-branch-reuse.md` (v1, kept for audit trail)
**Status:** ready after applying round-4 review notes

## Revision 2 (round-3 review)

Six blockers + three non-blockers folded in:

1. **`claim_next` actually enforced.** v2 claimed the queue already enforced one-running-task-per-project; it doesn't — `src/task_queue.py:96-108` only filters on `project_path` + `status='pending'`. Phase H.12 is now a fix-then-test task: add `AND NOT EXISTS (SELECT 1 FROM tasks r WHERE r.project_path=? AND r.status='running')` to `claim_next`'s SQL (+ extra `?` param), then the regression test passes. It also updates the existing `TestHighPriorityJumpsQueue` expectation, which currently documents the old permissive behavior.
2. **`current_branch()` normalizes detached HEAD.** `git rev-parse --abbrev-ref HEAD` prints the literal string `HEAD` when detached, not `""`. Phase E patches `src/git_ops.current_branch` to return `""` in that case, plus a real-git detached-HEAD test (not just mocked).
3. **`_prior_branch` guard tightened to strict equality.** v2's `if x and x != y` let `sender_agent=NULL` rows pass. Phase F now uses `if outbound.get("sender_agent") != agent_name: return ""` — fail closed.
4. **ACK no longer says "existing branch".** A reused branch can be deleted between enqueue and worker run; the matrix would silently fall through to fresh-branch creation while the ACK promised "existing". Phase F switches to "continue prior branch" — accurate either way without duplicating matrix logic.
5. **Classifier returns `None` for polite-only input.** v2's `classify_mutation("please")` returned `True` (stripped tokens empty → bias-to-mutating), contradicting the docstring + Phase H.14 coverage assertion. Phase B adds `if not stripped_tokens: return None` after the polite strip.
6. **Blocker: first-time read-only questions must not create branches.** A fresh task like `explain the schema` must be stamped `mutates_repo=0`, return no `planned_branch`, and run through `branch_prep` without calling `is_clean`, `checkout_new_branch`, or `set_branch`. This pins the user-facing bug where every harmless question forks a new branch.

Non-blockers:

- **Schema parity.** Migration becomes `ALTER TABLE outbound_emails ADD COLUMN task_id INTEGER REFERENCES tasks(id)` to match SCHEMA (Phase A.2).
- **Test counts.** All interim "expected ~1262" / "~1315" numbers replaced with "exact count varies; capture in Phase H" — they were inconsistent estimates that would confuse the executor.
- **`retry_task_tool` inherits intent.** Phase G gains a small sub-task: retries inherit `mutates_repo` AND `branch_name` from the original so retrying a read-only task stays read-only and continues on the same branch (instead of forking a fresh one).

---

## Why v2

Reviewer's second pass found four behavior bugs and three quality bugs in v1. v2 folds in every correction:

1. **`current_branch == prior` axis added to the branch matrix.** v1 short-circuited read-only before checking branch_name, so "explain what you changed" never reused the prior branch. v1 also blanket-failed mutating follow-ups on dirty repos — but the worker doesn't commit before `mark_done`, so the prior task's branch is *always* dirty when its follow-up arrives. v2's matrix treats "already on the prior branch" as the safe-to-continue case.
2. **First-time tasks now get classified.** v1 only classified replies, so the README claim "read-only tasks skip the dirty gate" was false for `explain the schema` arriving as a fresh email. v2 classifies in `enqueue_task_tool` when the caller passes no explicit hint.
3. **ACK text matches reality.** v1's ACK always reported a "planned branch" even when no branch would be created. v2 picks one of three sentences based on actual outcome.
4. **Task 10 dropped.** v1's chat_ask end-to-end test asserted against `tq.latest_task` which reads the *prior* row (replies to `ask` don't enqueue). The relay-stamping it claimed to cover is already covered by phase D's test.
5. **`ON CONFLICT DO UPDATE`** for `record_outbound_email` — v1's `DO NOTHING` silently lost `task_id` whenever a row was recorded twice (the relay's `set_email_message_id` → `record_outbound_email` order can produce this).
6. **Project/agent guard in `_prior_branch`** — verify `prior.project_path == decision.project_path` and `outbound.sender_agent == agent_name` so a misrouted reply can't inherit a branch from another project.
7. **Polite-prefix strip in the classifier** — "can you explain X" was misclassified as mutating because `can` isn't in `_READ_ONLY`. v2 strips `please|can you|could you|would you|will you|tell me|pls` before tokenizing while still letting `commit`-anywhere catch mutating verbs.
8. **Strict 200-line rule** — v1 allowed `task_queue.py` to slip to ~205. v2 splits redaction helpers into `src/task_row_redact.py` so every file stays under the cap.

## Goal

Stop blocking obviously-read-only tasks on a dirty repo, and make email follow-up replies continue on the original task's branch instead of forking a fresh branch each time.

## Architecture

Three coordinated primitives:

1. **`tasks.mutates_repo`** — `NULL` = unknown/gated (today's behavior), `1` = mutating (gated), `0` = read-only (skip gate). Stamped by a conservative regex classifier biased to "mutates" on ambiguity.
2. **`outbound_emails.task_id`** — links every relayed agent→user email back to its originating task so a user reply's `In-Reply-To` header can walk to the prior task and its `branch_name`.
3. **Branch matrix in `src/branch_prep.py`** — nine-cell decision based on `(is_git_repo, mutates_repo, prior_branch, current_branch == prior, is_clean)`.

### The matrix (the central change)

| `is_git_repo` | `mutates_repo` | `prior_branch` | `on prior?` | `is_clean` | Action |
|---|---|---|---|---|---|
| no | any | any | any | any | run, no branch |
| yes | False | none | — | any | run, skip dirty check, no branch |
| yes | True/NULL | none | — | clean | new branch, run |
| yes | True/NULL | none | — | dirty | **fail** |
| yes | any | set | yes (`current == prior`) | any | run on this branch (this is *our* dirt) |
| yes | any | set | no | clean | checkout prior, run |
| yes | any | set | no | dirty | **fail** (can't switch safely) |
| yes | any | set-but-missing | — | clean | fresh new branch (prior gone) |
| yes | any | set-but-missing | — | dirty | **fail** |

The `on prior?` axis is what unlocks both reviewer blockers — read-only follow-ups *do* checkout the prior branch when clean, and mutating follow-ups *do* continue on the prior branch's dirty tree because the dirt belongs to the previous task in the same chain.

## Tech stack

- Python 3.12 · sqlite3 (WAL) · pytest · MCP SSE (Starlette). No new dependencies.
- All subprocess calls `shell=False`.
- 200-line file cap **strict** — every file must stay ≤200 after this PR.
- 100% coverage on production code (`.coveragerc` omits tests, entry shim, pragma patterns).

## Repo invariants you must preserve

- **NULL preserves today's behavior.** `mutates_repo IS NULL` MUST behave identically to today's always-gated path. This is the safety net for the 1000+ rows already in `claude-chat.db`.
- **Schema changes go through SCHEMA + idempotent MIGRATIONS** in `src/chat_schema.py`. Never mutate `SCHEMA` without adding the equivalent `ALTER TABLE … ADD COLUMN` to `MIGRATIONS`.
- **No real emails in code.** Real addresses live in `.env` / `.env.test` only.
- **Run `.venv/bin/pytest tests/ -q` after every task.** Baseline is 1212 passing.

## File map

### Created (new files this PR)

| Path | Responsibility |
|------|----------------|
| `src/outbound_emails_store.py` | `OutboundEmailsMixin` — `record_outbound_email` (uses `ON CONFLICT DO UPDATE` to preserve `task_id`) + `find_outbound_email`. ~50 LOC. |
| `src/mutation_classifier.py` | `classify_mutation(body) -> bool \| None`. Strips polite prefixes; biased to "mutates" on ambiguity; `None` for empty or polite-only zero-signal input. ~90 LOC. |
| `src/task_row_redact.py` | `_REDACT_FROM_PUBLIC` + `public_row()` extracted from `task_queue.py` so the latter stays ≤200. ~20 LOC. |
| `src/branch_prep.py` | `prepare_branch(queue, task, project_path)` — the full nine-cell matrix. ~120 LOC. |
| `tests/test_outbound_emails_store.py` | Pin the mixin extraction. ~30 LOC. |
| `tests/test_chat_schema_migrations.py` | Fresh-DB SCHEMA + upgrade-from-old-DB MIGRATIONS. ~80 LOC. |
| `tests/test_mutation_classifier.py` | Read-only / mutating / ambiguous / polite-prefix cases. ~120 LOC. |
| `tests/test_task_row_redact.py` | Pin the redaction split. ~20 LOC. |
| `tests/test_branch_prep.py` | Full matrix coverage. ~200 LOC. |
| `tests/test_apply_reply_branch_reuse.py` | End-to-end reply lookup + project/agent mismatch guard. ~150 LOC. |
| `tests/test_one_running_per_branch.py` | Regression pin for the one-running-task-per-project invariant. ~30 LOC. |

### Modified

| Path | Change |
|------|--------|
| `src/chat_schema.py` | Add `tasks.mutates_repo INTEGER` and `outbound_emails.task_id INTEGER REFERENCES tasks(id)` to `SCHEMA`; append both ALTERs + an index to `MIGRATIONS`. |
| `src/chat_db.py` | Remove inlined outbound methods (moved to mixin); inherit `OutboundEmailsMixin`. |
| `src/task_queue.py` | Import `public_row` from `task_row_redact`; `enqueue()` gains `branch_name` and `mutates_repo`; `claim_next()` gains `NOT EXISTS running` guard (Phase H.12). |
| `src/git_ops.py` | Add `branch_exists()`, `checkout_existing_branch()`, `is_valid_task_branch()`; normalize `current_branch()` to return `""` on detached HEAD (Phase E.9). |
| `src/project_worker.py` | Delegate `_prepare_branch` body to `src.branch_prep.prepare_branch`. |
| `src/chat_relay.py` | `relay_outbound_messages` passes `task_id=msg.get("task_id")` to `record_outbound_email`. |
| `src/reply_router.py` | `apply_reply` walks `In-Reply-To` → `outbound_emails.task_id` → prior task → branch (with project/agent mismatch guard), classifies follow-up body, formats outcome-accurate ACK. |
| `chat/project_tools.py` | `enqueue_task_tool` accepts `mutates_repo`; auto-classifies when None; returns accurate `planned_branch` (empty for read-only). `retry_task_tool` inherits `mutates_repo` + `branch_name` from the original (Phase G.11b). |

### Touched indirectly

- `tests/test_chat_db.py` — confirm `find_outbound_email` returns `task_id` field.
- `tests/test_outbound_emails.py` — add `task_id` round-trip + `DO UPDATE` test.
- `tests/test_chat_relay.py` — assert `task_id` lands in `outbound_emails`.
- `tests/test_enqueue_task_tool.py` — assert auto-classify + accurate `planned_branch`.
- `tests/test_reply_router.py` — fake task queue accepts new kwargs.

### Out of scope (do NOT touch)

- The JSON envelope path (`src/json_handler/*`) — uses `enqueue_task_tool` already; inherits classification for free.
- The `enqueue_routed` virtual-task path — never spawns a worker, so the dirty check never runs.
- Any LLM-router changes. The classifier in this plan is regex-only.
- The website / README until everything is green (one combined doc-update task in Phase H).

## Phase index

| Phase | File | Tasks | Purpose |
|-------|------|-------|---------|
| A | [phase-a-schema-and-db.md](phase-a-schema-and-db.md) | 1, 2, 3 | Mixin extraction → schema columns → record_outbound_email with `DO UPDATE` + `task_id` |
| B | [phase-b-classifier.md](phase-b-classifier.md) | 4 | `classify_mutation` with polite-prefix strip |
| C | [phase-c-queue.md](phase-c-queue.md) | 5, 6 | Redaction split → `enqueue()` accepts `branch_name` + `mutates_repo` |
| D | [phase-d-relay.md](phase-d-relay.md) | 7 | Relay stamps `task_id` on every outbound that has one |
| E | [phase-e-branch-prep.md](phase-e-branch-prep.md) | 8, 9 | Extract `prepare_branch` → implement the nine-cell matrix |
| F | [phase-f-reply-routing.md](phase-f-reply-routing.md) | 10 | `apply_reply` with prior-branch lookup, project/agent guard, outcome-accurate ACK |
| G | [phase-g-enqueue-tool.md](phase-g-enqueue-tool.md) | 11 | `enqueue_task_tool` auto-classifies and reports accurate `planned_branch` |
| H | [phase-h-finalize.md](phase-h-finalize.md) | 12, 13, 14 | Concurrency-invariant test + docs + `/simplify` + coverage + final verification |

Total tasks: 14 (Phase G splits internally into G.11a + G.11b for the retry inheritance). Expected new tests: ~55–65. Final exact count captured in Phase H.14 — do not pin running totals in earlier phases (they vary with parametrize expansion and got inconsistent across v2's drafts).

## Risk notes

1. **Don't restart `claude-chat` to "test" the migration.** Per CLAUDE.md operational notes, that severs live MCP sessions and breaks the dashboard. The migration runs at `ChatDB.__init__` time — Phase H's smoke step covers the upgrade path on a copy of the DB.
2. **`MIGRATIONS` is append-only.** New entries go at the end of the list, never inserted between existing ones. Order matters because some `CREATE INDEX` lines depend on prior `ALTER TABLE` lines having added the column.
3. **The classifier is intentionally dumb.** Don't grow it into an LLM call without a separate spec. The whole safety story is "biased to mutating; NULL passes through to today's behavior."
4. **Branch-name validation is defense in depth, not security.** `git_ops` uses `shell=False`, so injection isn't the threat. The check guards against weird future bug rows in `tasks.branch_name`.
5. **The "on prior?" check uses `current_branch(project_path)`** — that returns `""` for detached HEAD. Treat detached HEAD as "not on prior" so the matrix falls through to the dirty/clean logic.

## Self-review checklist (run after Phase H)

- [ ] **Spec coverage.** Reviewer's blockers 1–6 + smaller issues a–e all addressed. Walk through them against the implemented matrix and tests.
- [ ] **First-time read-only blocker.** Verify `explain the schema` through `enqueue_task_tool` gets `mutates_repo=0`, returns no `planned_branch`, and `branch_prep.prepare_branch()` does not call `is_clean`, `checkout_new_branch`, or `set_branch`.
- [ ] **Placeholder scan.** `grep -rn 'TODO\|FIXME\|XXX' src/branch_prep.py src/mutation_classifier.py src/outbound_emails_store.py src/reply_router.py src/task_queue.py src/task_row_redact.py chat/project_tools.py` — empty.
- [ ] **Type consistency.** `branch_name: str` everywhere; `mutates_repo: bool | None` in Python, `INTEGER` (NULL/0/1) on disk.
- [ ] **Name consistency.** `prepare_branch`, `classify_mutation`, `find_outbound_email`, `branch_exists`, `checkout_existing_branch`, `is_valid_task_branch`, `public_row`.
- [ ] **No stale imports.** `grep -n 'from src.git_ops import .*checkout_new_branch' src/project_worker.py` — empty (moved to `branch_prep`).
- [ ] **Strict 200-line.** `scripts/check-line-limit.sh` — pass.
- [ ] **100% coverage.** `.venv/bin/pytest tests/ --cov=src --cov=chat --cov-report=term-missing` — no missed lines on production code.

## When done

Final commit list should read as a clean story (one commit per phase or sub-task):

```
refactor(chat_db): extract OutboundEmailsMixin                      [Phase A.1]
feat(schema): add tasks.mutates_repo and outbound_emails.task_id    [Phase A.2]
feat(outbound): record_outbound_email accepts task_id (DO UPDATE)   [Phase A.3]
feat: conservative mutation classifier with polite-prefix strip     [Phase B]
refactor(queue): split _REDACT_FROM_PUBLIC into task_row_redact     [Phase C.5]
feat(queue): enqueue accepts branch_name and mutates_repo           [Phase C.6]
feat(relay): stamp task_id on outbound emails                       [Phase D]
refactor(worker): extract prepare_branch into src/branch_prep.py    [Phase E.8]
feat(worker): branch_prep nine-cell matrix with current-branch axis [Phase E.9]
feat(reply): walk outbound→prior task; project/agent guard; honest ACK [Phase F]
feat(mcp): enqueue_task_tool auto-classifies; planned_branch is honest [Phase G]
fix(queue): enforce one running task per project                    [Phase H.12]
docs: dirty-gate skip + branch reuse for follow-ups                 [Phase H.13]
style: post-simplify cleanup (only if applied)                      [Phase H.14]
```

Hand back to the user with the test count, file list, and proposed PR title:

> `fix: skip dirty-repo gate for read-only tasks; reuse prior branch on email follow-ups`
