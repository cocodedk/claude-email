# Consolidated v2.1 Plan — Fix Dirty-Block & Branch Reuse

> **Flattened view of the 9-file plan in `2026-05-17-fix-dirty-block-and-branch-reuse-v2/`.**
> Source of truth is the folder; this file is for review only. Each section below is bracketed with `BEGIN FILE` / `END FILE` markers naming the original.

**Version:** v2.1 (2026-05-17 — round-3 reviewer fixes folded into v2 in place)
**What changed since v2:** see the "Revision 2 (round-3 review)" section in README.md below — 5 blockers + 3 non-blockers patched across phases A, B, E, F, G, H.
**Files included:** 9 (1 README + 8 phase files)
**Total tasks:** 14 (Phase G has sub-tasks 11a + 11b)

---


================================================================================
BEGIN FILE: README.md
================================================================================

# Fix Dirty-Repo Blocking & Branch Reuse — Plan v2

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Each phase file lists tasks as `- [ ]` checkboxes; execute in numbered order across phase files. Phases A→H map to files `phase-a-*.md` … `phase-h-*.md` in this folder.

**Version:** v2 (timestamp 2026-05-17), revision 2 folded in same date
**Supersedes:** `../2026-05-17-fix-dirty-block-and-branch-reuse.md` (v1, kept for audit trail)
**Status:** ready for execution

## Revision 2 (round-3 review)

Five blockers + three non-blockers folded in:

1. **`claim_next` actually enforced.** v2 claimed the queue already enforced one-running-task-per-project; it doesn't — `src/task_queue.py:96-108` only filters on `project_path` + `status='pending'`. Phase H.12 is now a fix-then-test task: add `AND NOT EXISTS (SELECT 1 FROM tasks r WHERE r.project_path=? AND r.status='running')` to `claim_next`'s SQL (+ extra `?` param), then the regression test passes.
2. **`current_branch()` normalizes detached HEAD.** `git rev-parse --abbrev-ref HEAD` prints the literal string `HEAD` when detached, not `""`. Phase E patches `src/git_ops.current_branch` to return `""` in that case, plus a real-git detached-HEAD test (not just mocked).
3. **`_prior_branch` guard tightened to strict equality.** v2's `if x and x != y` let `sender_agent=NULL` rows pass. Phase F now uses `if outbound.get("sender_agent") != agent_name: return ""` — fail closed.
4. **ACK no longer says "existing branch".** A reused branch can be deleted between enqueue and worker run; the matrix would silently fall through to fresh-branch creation while the ACK promised "existing". Phase F switches to "continue prior branch" — accurate either way without duplicating matrix logic.
5. **Classifier returns `None` for polite-only input.** v2's `classify_mutation("please")` returned `True` (stripped tokens empty → bias-to-mutating), contradicting the docstring + Phase H.14 coverage assertion. Phase B adds `if not stripped_tokens: return None` after the polite strip.

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
| `src/mutation_classifier.py` | `classify_mutation(body) -> bool \| None`. Strips polite prefixes; biased to "mutates" on ambiguity; `None` only for empty body. ~90 LOC. |
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

- [ ] **Spec coverage.** Reviewer's blockers 1–5 + smaller issues a–e all addressed. Walk through them against the implemented matrix and tests.
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
test: pin one-running-task-per-project invariant                    [Phase H.12]
docs: dirty-gate skip + branch reuse for follow-ups                 [Phase H.13]
style: post-simplify cleanup (only if applied)                      [Phase H.14]
```

Hand back to the user with the test count, file list, and proposed PR title:

> `fix: skip dirty-repo gate for read-only tasks; reuse prior branch on email follow-ups`


================================================================================
END FILE: README.md
================================================================================


================================================================================
BEGIN FILE: phase-a-schema-and-db.md
================================================================================

# Phase A — Schema + DB primitives

Three tasks. Each ends with a green pytest run and a commit.

- **A.1** Extract `OutboundEmailsMixin` (move-only refactor) so `chat_db.py` has headroom for new outbound fields.
- **A.2** Add `tasks.mutates_repo` and `outbound_emails.task_id` to `SCHEMA` and `MIGRATIONS`.
- **A.3** Extend `record_outbound_email` to accept `task_id`; switch `ON CONFLICT` to `DO UPDATE` so a later non-NULL `task_id` is preserved.

Phase A leaves no behavioral change visible outside the storage layer. Phases B–G consume the new columns.

---

## Task A.1: Extract `OutboundEmailsMixin`

**Files:**
- Create: `src/outbound_emails_store.py`
- Modify: `src/chat_db.py:147-174` (remove these two methods)
- Modify: `src/chat_db.py:5-12` (add mixin import); `src/chat_db.py:20-23` (add to base list)
- Test: `tests/test_outbound_emails_store.py` (create)

This is a pure move so Task A.3 can extend the methods without pushing `chat_db.py` over the 200-line cap.

- [ ] **Step 1: Write the failing test**

`tests/test_outbound_emails_store.py`:

```python
"""ChatDB inherits OutboundEmailsMixin — moving these two methods out of
chat_db.py keeps the host file under the 200-line cap. This test pins
the public surface so the move is verifiably behavior-preserving."""
from src.chat_db import ChatDB
from src.outbound_emails_store import OutboundEmailsMixin


def test_chatdb_inherits_outbound_mixin():
    assert issubclass(ChatDB, OutboundEmailsMixin)


def test_record_and_find_still_work(tmp_path):
    cdb = ChatDB(str(tmp_path / "x.db"))
    cdb.record_outbound_email("<m@x>", kind="ack", sender_agent="agent-x")
    row = cdb.find_outbound_email("<m@x>")
    assert row["kind"] == "ack"
    assert row["sender_agent"] == "agent-x"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_outbound_emails_store.py -v`
Expected: FAIL on `from src.outbound_emails_store import OutboundEmailsMixin` — `ModuleNotFoundError`.

- [ ] **Step 3: Create the mixin file**

`src/outbound_emails_store.py`:

```python
"""Outbound SMTP Message-ID store — extracted from chat_db.py.

Every reply we send (relay, ACK, JSON envelope, CLI-fallback) records
here so a user reply passes ``security.is_authorized`` via the
chat-thread match without an ``AUTH:`` keyword. Lives in its own
mixin so chat_db.py stays under the 200-line cap as task_id and any
future per-outbound metadata land on this table.
"""
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class OutboundEmailsMixin:
    """Persist + look up SMTP Message-IDs we have sent."""

    def record_outbound_email(
        self, email_message_id: str, *, kind: str, sender_agent: str = "",
    ) -> None:
        if not email_message_id:
            raise ValueError("email_message_id must not be empty")
        self._conn.execute(
            "INSERT INTO outbound_emails "
            "(email_message_id, sent_at, kind, sender_agent) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(email_message_id) DO NOTHING",
            (email_message_id, _now(), kind, sender_agent or None),
        )
        self._conn.commit()

    def find_outbound_email(self, email_message_id: str) -> dict | None:
        if not email_message_id:
            return None
        row = self._conn.execute(
            "SELECT * FROM outbound_emails WHERE email_message_id=?",
            (email_message_id,),
        ).fetchone()
        return dict(row) if row else None
```

Task A.3 will modify both methods. This task only moves them verbatim.

- [ ] **Step 4: Update `src/chat_db.py` imports + base list**

Replace `src/chat_db.py:5-12`:

```python
from src.agent_registry import AgentRegistryMixin
from src.agent_state import AgentStateMixin
from src.chat_errors import AgentNameTaken, AgentProjectTaken
from src.chat_schema import MIGRATIONS as _MIGRATIONS, SCHEMA as _SCHEMA
from src.dashboard_queries import DashboardQueriesMixin
from src.db_maintenance import MaintenanceMixin
from src.outbound_emails_store import OutboundEmailsMixin
from src.wake_session_store import WakeSessionStoreMixin
```

Replace `src/chat_db.py:20-23`:

```python
class ChatDB(
    AgentRegistryMixin, AgentStateMixin, DashboardQueriesMixin,
    MaintenanceMixin, OutboundEmailsMixin, WakeSessionStoreMixin,
):
```

- [ ] **Step 5: Delete `record_outbound_email` + `find_outbound_email` from `src/chat_db.py`**

Delete `src/chat_db.py:147-174` entirely (the two outbound methods + their docstrings).

- [ ] **Step 6: Run tests + line check**

```
.venv/bin/pytest tests/test_outbound_emails_store.py tests/test_outbound_emails.py tests/test_chat_db.py -v
.venv/bin/pytest tests/ -q
scripts/check-line-limit.sh
```

Expected: all pass; no file >200 lines. (Exact test count varies with parametrize expansion; capture the final total in Phase H.14.)

- [ ] **Step 7: Commit**

```bash
git add src/outbound_emails_store.py src/chat_db.py tests/test_outbound_emails_store.py
git commit -m "refactor(chat_db): extract OutboundEmailsMixin to free 200-line headroom"
```

---

## Task A.2: Add `tasks.mutates_repo` + `outbound_emails.task_id`

**Files:**
- Modify: `src/chat_schema.py:41-67` (add `mutates_repo` to tasks)
- Modify: `src/chat_schema.py:75-81` (add `task_id` to outbound_emails)
- Modify: `src/chat_schema.py:85-103` (append ALTER + INDEX migrations)
- Test: `tests/test_chat_schema_migrations.py` (create)

- [ ] **Step 1: Write the failing test**

`tests/test_chat_schema_migrations.py`:

```python
"""Schema migration tests for the dirty-block / branch-reuse fix.

Both columns must be present after ChatDB() construction — fresh-DB
path (SCHEMA) and pre-existing-DB path (MIGRATIONS). NULL is the
default so existing rows stay safety-gated."""
import sqlite3

from src.chat_db import ChatDB


def _columns(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


class TestFreshDB:
    def test_tasks_has_mutates_repo(self, tmp_path):
        cdb = ChatDB(str(tmp_path / "a.db"))
        assert "mutates_repo" in _columns(cdb._conn, "tasks")

    def test_outbound_emails_has_task_id(self, tmp_path):
        cdb = ChatDB(str(tmp_path / "b.db"))
        assert "task_id" in _columns(cdb._conn, "outbound_emails")

    def test_mutates_repo_defaults_to_null(self, tmp_path):
        cdb = ChatDB(str(tmp_path / "c.db"))
        cdb._conn.execute(
            "INSERT INTO tasks (project_path, body, created_at) "
            "VALUES ('/p', 'x', '2026-05-17T00:00:00+00:00')"
        )
        cdb._conn.commit()
        row = cdb._conn.execute("SELECT mutates_repo FROM tasks LIMIT 1").fetchone()
        assert row["mutates_repo"] is None

    def test_outbound_task_id_index_exists(self, tmp_path):
        cdb = ChatDB(str(tmp_path / "d.db"))
        rows = cdb._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='outbound_emails'"
        ).fetchall()
        names = {r["name"] for r in rows}
        assert "outbound_emails_task_id_idx" in names


class TestUpgradeExistingDB:
    """Simulate a deployed DB that pre-dates the new columns."""

    def _make_old_db(self, path):
        conn = sqlite3.connect(path)
        conn.executescript("""
            CREATE TABLE tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_path TEXT NOT NULL,
                body TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                priority INTEGER NOT NULL DEFAULT 0,
                pid INTEGER,
                created_at TEXT NOT NULL
            );
            CREATE TABLE outbound_emails (
                email_message_id TEXT PRIMARY KEY,
                sent_at TEXT NOT NULL,
                kind TEXT NOT NULL,
                sender_agent TEXT
            );
        """)
        conn.commit()
        conn.close()

    def test_migrations_add_new_columns(self, tmp_path):
        path = str(tmp_path / "old.db")
        self._make_old_db(path)
        cdb = ChatDB(path)  # triggers migrations
        assert "mutates_repo" in _columns(cdb._conn, "tasks")
        assert "task_id" in _columns(cdb._conn, "outbound_emails")

    def test_migrations_are_idempotent(self, tmp_path):
        path = str(tmp_path / "old2.db")
        self._make_old_db(path)
        ChatDB(path)  # first migrate
        ChatDB(path)  # second open — must not raise
```

- [ ] **Step 2: Run test to verify failures**

Run: `.venv/bin/pytest tests/test_chat_schema_migrations.py -v`
Expected: FAIL — columns don't exist yet.

- [ ] **Step 3: Update SCHEMA**

In `src/chat_schema.py:41-67`, change the `tasks` block to include `mutates_repo INTEGER` after `branch_name`:

```python
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_path TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    priority INTEGER NOT NULL DEFAULT 0,
    pid INTEGER,
    branch_name TEXT,
    mutates_repo INTEGER,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    error_text TEXT,
    output_text TEXT,
    retry_of INTEGER,
    plan_first INTEGER NOT NULL DEFAULT 0,
    origin_content_type TEXT,
    origin_message_id TEXT,
    origin_subject TEXT,
    origin_from TEXT,
    dispatch_token TEXT,
    last_sent_status TEXT,
    origin_envelope_v INTEGER
);
```

In `src/chat_schema.py:75-81`, change the `outbound_emails` block:

```python
CREATE TABLE IF NOT EXISTS outbound_emails (
    email_message_id TEXT PRIMARY KEY,
    sent_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    sender_agent TEXT,
    task_id INTEGER REFERENCES tasks(id)
);
```

- [ ] **Step 4: Append MIGRATIONS (append-only)**

Append at the end of `src/chat_schema.py:85-103`:

```python
    "ALTER TABLE tasks ADD COLUMN mutates_repo INTEGER",
    "ALTER TABLE outbound_emails ADD COLUMN task_id INTEGER REFERENCES tasks(id)",
    "CREATE INDEX IF NOT EXISTS outbound_emails_task_id_idx "
    "ON outbound_emails(task_id) WHERE task_id IS NOT NULL",
```

The `try/except sqlite3.OperationalError: pass` block in `chat_db.py:34-38` already swallows "duplicate column" errors, so idempotency holds.

Note on `REFERENCES`: SQLite permits adding a nullable column with `REFERENCES` in `ALTER TABLE`. This keeps the migrated schema byte-identical to the SCHEMA defined for fresh DBs, satisfying the "SCHEMA + equivalent migration" invariant.

- [ ] **Step 5: Run tests**

```
.venv/bin/pytest tests/test_chat_schema_migrations.py -v
.venv/bin/pytest tests/ -q
```

Expected: all pass. (Exact count varies; capture in Phase H.14.)

- [ ] **Step 6: Commit**

```bash
git add src/chat_schema.py tests/test_chat_schema_migrations.py
git commit -m "feat(schema): add tasks.mutates_repo and outbound_emails.task_id"
```

---

## Task A.3: `record_outbound_email` accepts `task_id` + uses `DO UPDATE`

**Files:**
- Modify: `src/outbound_emails_store.py`
- Modify: `tests/test_outbound_emails.py` (add 4 cases)

The reviewer's catch on this is load-bearing: when the relay does `set_email_message_id` and then `record_outbound_email`, or when a duplicate-send happens, `ON CONFLICT DO NOTHING` would silently drop a later non-NULL `task_id`. `DO UPDATE SET task_id = COALESCE(...)` keeps the first non-NULL value seen.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_outbound_emails.py` inside `class TestRecordOutbound`:

```python
    def test_record_with_task_id(self, cdb):
        cdb.record_outbound_email(
            "<t1@x>", kind="result", sender_agent="agent-y", task_id=42,
        )
        row = cdb.find_outbound_email("<t1@x>")
        assert row["task_id"] == 42

    def test_record_without_task_id_stores_null(self, cdb):
        cdb.record_outbound_email("<t2@x>", kind="ack")
        row = cdb.find_outbound_email("<t2@x>")
        assert row["task_id"] is None

    def test_later_task_id_fills_null(self, cdb):
        """Common case: relay records the ID first (no task_id), then a
        follow-up code path records again with the real task_id. The
        DO UPDATE / COALESCE pair must let the non-NULL win."""
        cdb.record_outbound_email("<t3@x>", kind="ack")  # task_id=NULL
        cdb.record_outbound_email("<t3@x>", kind="ack", task_id=99)
        assert cdb.find_outbound_email("<t3@x>")["task_id"] == 99

    def test_existing_task_id_is_not_overwritten(self, cdb):
        """If a row already has task_id=A, a later record with task_id=B
        must keep A. Same email_message_id should never legitimately
        belong to two tasks; keep the first."""
        cdb.record_outbound_email("<t4@x>", kind="ack", task_id=5)
        cdb.record_outbound_email("<t4@x>", kind="ack", task_id=7)
        assert cdb.find_outbound_email("<t4@x>")["task_id"] == 5
```

- [ ] **Step 2: Run tests to verify failures**

Run: `.venv/bin/pytest tests/test_outbound_emails.py::TestRecordOutbound -v`
Expected: FAIL — `TypeError: record_outbound_email() got an unexpected keyword argument 'task_id'`.

- [ ] **Step 3: Extend `record_outbound_email`**

Replace the method in `src/outbound_emails_store.py`:

```python
    def record_outbound_email(
        self, email_message_id: str, *, kind: str, sender_agent: str = "",
        task_id: int | None = None,
    ) -> None:
        if not email_message_id:
            raise ValueError("email_message_id must not be empty")
        self._conn.execute(
            "INSERT INTO outbound_emails "
            "(email_message_id, sent_at, kind, sender_agent, task_id) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(email_message_id) DO UPDATE SET "
            "task_id = COALESCE(outbound_emails.task_id, excluded.task_id)",
            (email_message_id, _now(), kind, sender_agent or None, task_id),
        )
        self._conn.commit()
```

`find_outbound_email` needs no change — `SELECT *` surfaces the new column once Task A.2 has run.

`kind` and `sender_agent` are intentionally NOT updated by the conflict path. The first send establishes the row's "shape"; later sends only need to fill in metadata that was unknown earlier (currently just `task_id`).

- [ ] **Step 4: Run tests**

```
.venv/bin/pytest tests/test_outbound_emails.py -v
.venv/bin/pytest tests/ -q
```

Expected: all pass. (Exact count varies; capture in Phase H.14.)

- [ ] **Step 5: Commit**

```bash
git add src/outbound_emails_store.py tests/test_outbound_emails.py
git commit -m "feat(outbound): record_outbound_email accepts task_id (DO UPDATE preserves it)"
```


================================================================================
END FILE: phase-a-schema-and-db.md
================================================================================


================================================================================
BEGIN FILE: phase-b-classifier.md
================================================================================

# Phase B — Mutation classifier with polite-prefix strip

One task. Adds the deterministic regex classifier that downstream phases use to stamp `mutates_repo` on tasks.

The classifier returns:
- `True` — mutating intent detected (clearly mutating verb anywhere)
- `False` — clearly read-only (leading read-only verb or interrogative *after* stripping politeness)
- `None` — zero signal (empty body); caller leaves the column NULL so the worker falls back to today's gated behavior

The polite-prefix strip is the reviewer's catch — "can you explain X" was misclassified as mutating in v1 because `can` is not in `_READ_ONLY`. Stripping `please|can you|could you|would you|will you|tell me|pls` before tokenizing makes "can you explain X" → "explain X" → read-only. The "mutating verb anywhere" check still runs first, so "can you commit the changes?" stays mutating via `commit`.

---

## Task B.4: `src/mutation_classifier.py`

**Files:**
- Create: `src/mutation_classifier.py`
- Create: `tests/test_mutation_classifier.py`

- [ ] **Step 1: Write the failing test**

`tests/test_mutation_classifier.py`:

```python
"""Tests for src/mutation_classifier.py — read-only vs mutating intent.

The classifier biases to 'mutates' on ambiguity. NULL (the None return)
is reserved for bodies with zero signal so existing rows + ambiguous
new rows stay safety-gated by the worker."""
import pytest

from src.mutation_classifier import classify_mutation


class TestReadOnly:
    @pytest.mark.parametrize("body", [
        "explain how the bus reaper works",
        "show the last 5 commits on this branch",
        "list the agents currently registered",
        "where is the dispatch token validated?",
        "what is mutates_repo for?",
        "why did task 17 fail?",
        "status",
        "summarize the diff between HEAD and main",
        "read src/chat_relay.py and tell me what it does",
        "inspect the outbound_emails table",
        "How many tasks ran today?",
        "describe the schema",
    ])
    def test_obvious_read_only_returns_false(self, body):
        assert classify_mutation(body) is False


class TestPolitePrefixStrip:
    """v1 reviewer catch: 'can you explain X' is read-only. The polite
    prefix is stripped before classification, but the mutating-verb
    check still runs against the *original* body so 'can you commit X'
    stays mutating via the verb anywhere rule."""

    @pytest.mark.parametrize("body", [
        "can you explain the relay?",
        "could you show me the schema?",
        "would you list the agents?",
        "please describe the bus",
        "tell me what changed in task 17",
        "Pls show recent commits",
    ])
    def test_polite_read_only_returns_false(self, body):
        assert classify_mutation(body) is False

    @pytest.mark.parametrize("body", [
        "can you commit the changes?",
        "please push the branch",
        "could you delete the stale row?",
        "would you rewrite this please",
        "tell me to fix the bus",  # 'fix' anywhere -> mutating
    ])
    def test_polite_mutating_still_returns_true(self, body):
        assert classify_mutation(body) is True


class TestMutating:
    @pytest.mark.parametrize("body", [
        "fix the dirty-repo gate",
        "implement the classifier",
        "add a column to outbound_emails",
        "update the README",
        "refactor chat_handlers into two files",
        "delete the stale wake row for agent-x",
        "rename branch_name to per_task_branch",
        "change the default priority to 5",
        "commit these changes",
        "push the current branch",
        "rewrite the relay loop",
        "build the dashboard CSS bundle",
        "create a new agent for the search service",
        "drop the test database",
        "merge master into this branch",
    ])
    def test_obvious_mutating_returns_true(self, body):
        assert classify_mutation(body) is True


class TestAmbiguity:
    def test_empty_returns_none(self):
        assert classify_mutation("") is None
        assert classify_mutation("   \n\t ") is None

    def test_no_signal_returns_true_not_none(self):
        # 'thinking about X' has no read-only verb and no mutating verb,
        # but it's not zero-signal — body has content. Bias to mutates.
        assert classify_mutation("thinking about the architecture") is True

    def test_mixed_signals_bias_to_mutating(self):
        assert classify_mutation("explain why we should fix the bus") is True

    def test_imperative_inside_question_still_mutates(self):
        assert classify_mutation("can you commit the changes?") is True


class TestCaseAndPunctuation:
    def test_case_insensitive(self):
        assert classify_mutation("EXPLAIN the bus") is False
        assert classify_mutation("FIX the bus") is True

    def test_punctuation_tolerated(self):
        assert classify_mutation("explain: how does this work?") is False
        assert classify_mutation("fix: stop the leak") is True

    def test_leading_imperative_required_for_read_only(self):
        # Mutating verb later in body still wins.
        assert classify_mutation("rewrite this to explain better") is True


class TestStripIdempotent:
    """Multiple polite prefixes stack — 'please can you explain' should
    still strip down to 'explain'."""

    def test_stacked_prefixes_strip(self):
        assert classify_mutation("please can you explain the relay") is False
        assert classify_mutation("could you please show me the schema") is False


class TestPoliteOnlyReturnsNone:
    """Round-3 reviewer catch: body that is *only* a polite prefix
    (no verb at all after stripping) is zero-signal and must return
    None so the row stays NULL-gated, not bias-to-mutating."""

    @pytest.mark.parametrize("body", [
        "please",
        "Please.",
        "pls",
        "can you",
        "could you please",
        "would you",
    ])
    def test_polite_only_returns_none(self, body):
        assert classify_mutation(body) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_mutation_classifier.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.mutation_classifier'`.

- [ ] **Step 3: Implement the classifier**

`src/mutation_classifier.py`:

```python
"""Conservative read-only vs mutating intent classifier for task bodies.

Server-side, regex-only, biased to "mutates" on any ambiguity. Output
maps directly to ``tasks.mutates_repo``:

    True  → row stamped 1 → worker behaves as today (clean + new branch
                            or reuse prior branch if set)
    False → row stamped 0 → worker skips dirty check + skips new branch
                            (but still checks out prior branch if set,
                            see src.branch_prep)
    None  → row stays NULL → worker behaves as today (gated)

The NULL pass-through is what protects the 1000+ existing task rows and
any genuinely ambiguous future input from a behavior change.

Decision order:
  1. Empty / whitespace-only body                 → None
  2. Any mutating verb anywhere in the body       → True
  3. After stripping polite prefixes:
       a. nothing left (polite-only input)        → None
       b. first token is read-only / interrogative → False
       c. otherwise                                → True (bias to mutates)
"""
import re

_MUTATING = frozenset({
    "implement", "create", "fix", "add", "build", "run", "deploy",
    "push", "merge", "refactor", "update", "delete", "remove",
    "rename", "change", "commit", "stash", "rollback", "revert",
    "rebase", "install", "configure", "rewrite", "drop", "write",
    "modify", "edit", "patch", "scaffold", "generate", "ship",
    "bump", "upgrade", "migrate", "regenerate", "replace",
})

_READ_ONLY = frozenset({
    "explain", "show", "list", "describe", "summarize", "summarise",
    "read", "inspect", "report", "audit", "status", "tell", "print",
    "display", "find",
    "what", "which", "how", "why", "when", "where", "who",
})

# Polite prefixes are stripped (greedily, repeatedly) before step 3.
# Sorted by length descending so 'could you' wins over 'could'.
_POLITE_PREFIXES = (
    "could you please", "would you please", "can you please",
    "please can you", "please could you", "please would you",
    "could you", "would you", "can you", "will you",
    "tell me to", "tell me", "please", "pls",
)

_TOKEN_RE = re.compile(r"[a-zA-Z]+")


def _tokens(body: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(body)]


def _strip_polite(body: str) -> str:
    """Repeatedly strip leading polite prefixes (case-insensitive)."""
    s = body.strip().lower()
    while True:
        before = s
        for prefix in _POLITE_PREFIXES:
            if s.startswith(prefix + " ") or s == prefix:
                s = s[len(prefix):].lstrip(" ,:")
                break
        if s == before:
            return s


def classify_mutation(body: str) -> bool | None:
    """Return True (mutating), False (read-only), or None (no signal)."""
    tokens = _tokens(body)
    if not tokens:
        return None
    if any(t in _MUTATING for t in tokens):
        return True
    stripped_tokens = _tokens(_strip_polite(body))
    if not stripped_tokens:
        return None  # polite-only input — zero signal
    if stripped_tokens[0] in _READ_ONLY:
        return False
    return True
```

Note on step 2 placement: it runs against the *original* body's tokens, before the polite strip. That's intentional — a mutating verb anywhere ("can you commit") must win regardless of phrasing. The polite strip only governs the leading-token check.

Note on step 3a (the `if not stripped_tokens: return None`): this is the round-3 reviewer fix. Without it, `classify_mutation("please")` falls through to step 4 (bias-to-mutating) and returns `True`, which contradicts the docstring's "None for zero signal" claim and stamps a politeness-only message as mutating. Returning `None` keeps the row NULL-gated and falls back to today's behavior.

- [ ] **Step 4: Run tests**

```
.venv/bin/pytest tests/test_mutation_classifier.py -v
.venv/bin/pytest tests/ -q
```

Expected: all PASS. (Exact count varies with parametrize expansion; capture in Phase H.14.)

- [ ] **Step 5: Commit**

```bash
git add src/mutation_classifier.py tests/test_mutation_classifier.py
git commit -m "feat: conservative mutation classifier with polite-prefix strip"
```


================================================================================
END FILE: phase-b-classifier.md
================================================================================


================================================================================
BEGIN FILE: phase-c-queue.md
================================================================================

# Phase C — Queue layer

Two tasks. Splits the redaction helpers out of `task_queue.py` (strict 200-line cap), then extends `enqueue()` to accept `branch_name` and `mutates_repo` atomically.

- **C.5** Move `_REDACT_FROM_PUBLIC` + `_public` to `src/task_row_redact.py` so `task_queue.py` keeps headroom.
- **C.6** Extend `TaskQueue.enqueue()` with `branch_name` and `mutates_repo` kwargs; INSERT carries them.

Atomic insertion (instead of post-hoc `set_branch`) prevents a worker that claims the row from ever seeing a partial state.

---

## Task C.5: Extract `task_row_redact`

**Files:**
- Create: `src/task_row_redact.py`
- Modify: `src/task_queue.py:21-31` (remove `_REDACT_FROM_PUBLIC` + `_public`)
- Modify: `src/task_queue.py:13-15` (import `public_row`)
- Modify: all `_public(...)` call sites in `src/task_queue.py` → `public_row(...)`
- Test: `tests/test_task_row_redact.py` (create)

- [ ] **Step 1: Write the failing test**

`tests/test_task_row_redact.py`:

```python
"""Pin the redaction extraction. public_row must strip every key in
_REDACT_FROM_PUBLIC so dispatch_token (a bearer credential) never
leaves the DB layer."""
from src.task_row_redact import _REDACT_FROM_PUBLIC, public_row


def test_redact_set_includes_dispatch_token():
    assert "dispatch_token" in _REDACT_FROM_PUBLIC


def test_public_row_strips_redacted_keys():
    row = {"id": 1, "body": "x", "dispatch_token": "secret"}
    out = public_row(row)
    assert "dispatch_token" not in out
    assert out["id"] == 1
    assert out["body"] == "x"


def test_public_row_passes_through_unredacted_keys():
    row = {"id": 1, "branch_name": "claude/task-1-x", "mutates_repo": 0}
    out = public_row(row)
    assert out == row
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_task_row_redact.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.task_row_redact'`.

- [ ] **Step 3: Create the helper module**

`src/task_row_redact.py`:

```python
"""Row-redaction helpers for the task queue.

Extracted from src/task_queue.py to keep that file under the 200-line
cap. dispatch_token is a bearer token — knowing it lets a caller
inject their own enqueue into the email-router's correlation window,
so it must never leave the DB layer.
"""

_REDACT_FROM_PUBLIC = ("dispatch_token",)


def public_row(row: dict) -> dict:
    """Drop bearer-token columns from a task row before it leaves the DB layer."""
    return {k: v for k, v in row.items() if k not in _REDACT_FROM_PUBLIC}
```

- [ ] **Step 4: Update `src/task_queue.py`**

In `src/task_queue.py:13-15`, replace the imports block — drop the local `_REDACT_FROM_PUBLIC` constant and `_public` helper, import `public_row`:

```python
import sqlite3
from datetime import datetime, timezone

from src.task_row_redact import public_row
```

Delete lines `src/task_queue.py:21-31` (the comment + constant + `_public` function).

Rename every call site — `s/_public(/public_row(/g` in `src/task_queue.py`. There are ~8 occurrences: in `claim_next`, `list_pending`, `get_running`, `list_running`, `drain_pending` (rowcount return doesn't use it), `get`, `latest_task`. Use `grep -n _public src/task_queue.py` to find them all.

- [ ] **Step 5: Run tests + line check**

```
.venv/bin/pytest tests/test_task_row_redact.py tests/test_task_queue.py -v
.venv/bin/pytest tests/ -q
scripts/check-line-limit.sh
wc -l src/task_queue.py
```

Expected: all PASS; `src/task_queue.py` under 200 lines. (Exact count varies; capture in Phase H.14.)

- [ ] **Step 6: Commit**

```bash
git add src/task_row_redact.py src/task_queue.py tests/test_task_row_redact.py
git commit -m "refactor(queue): split _REDACT_FROM_PUBLIC into task_row_redact"
```

---

## Task C.6: `TaskQueue.enqueue()` accepts `branch_name` + `mutates_repo`

**Files:**
- Modify: `src/task_queue.py:42-61`
- Modify: `tests/test_task_queue.py` (add 5 cases)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_task_queue.py` inside `class TestEnqueue`:

```python
    def test_enqueue_persists_branch_name(self, tq):
        tid = tq.enqueue("/p", "follow up", branch_name="claude/task-17-fix-bus")
        assert tq.get(tid)["branch_name"] == "claude/task-17-fix-bus"

    def test_enqueue_branch_name_defaults_to_null(self, tq):
        tid = tq.enqueue("/p", "do thing")
        assert tq.get(tid)["branch_name"] is None

    def test_enqueue_persists_mutates_repo_true(self, tq):
        tid = tq.enqueue("/p", "fix it", mutates_repo=True)
        assert tq.get(tid)["mutates_repo"] == 1

    def test_enqueue_persists_mutates_repo_false(self, tq):
        tid = tq.enqueue("/p", "show me", mutates_repo=False)
        assert tq.get(tid)["mutates_repo"] == 0

    def test_enqueue_mutates_repo_defaults_to_null(self, tq):
        tid = tq.enqueue("/p", "anything")
        assert tq.get(tid)["mutates_repo"] is None
```

- [ ] **Step 2: Run tests to verify failures**

Run: `.venv/bin/pytest tests/test_task_queue.py::TestEnqueue::test_enqueue_persists_branch_name -v`
Expected: FAIL — `TypeError: enqueue() got an unexpected keyword argument 'branch_name'`.

- [ ] **Step 3: Extend `TaskQueue.enqueue`**

Replace `src/task_queue.py:42-61`:

```python
    def enqueue(
        self, project_path: str, body: str, priority: int = 0,
        retry_of: int | None = None, plan_first: bool = False,
        origin_content_type: str = "", origin_message_id: str = "",
        origin_subject: str = "", origin_from: str = "",
        dispatch_token: str = "", origin_envelope_v: int | None = None,
        branch_name: str = "", mutates_repo: bool | None = None,
    ) -> int:
        mut = None if mutates_repo is None else (1 if mutates_repo else 0)
        cur = self._conn.execute(
            "INSERT INTO tasks (project_path, body, priority, created_at, retry_of, "
            "plan_first, origin_content_type, origin_message_id, origin_subject, "
            "origin_from, dispatch_token, origin_envelope_v, branch_name, mutates_repo) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (project_path, body, priority, _now(), retry_of,
             1 if plan_first else 0, origin_content_type or None,
             origin_message_id or None, origin_subject or None,
             origin_from or None, dispatch_token or None,
             origin_envelope_v, branch_name or None, mut),
        )
        self._conn.commit()
        return cur.lastrowid
```

The INSERT writes `branch_name` and `mutates_repo` atomically with the rest of the row so a worker claiming it never sees a partial row.

- [ ] **Step 4: Run tests + line check**

```
.venv/bin/pytest tests/test_task_queue.py -v
.venv/bin/pytest tests/ -q
scripts/check-line-limit.sh
```

Expected: all PASS; `src/task_queue.py` still under 200 lines (≈195). (Exact count varies; capture in Phase H.14.)

- [ ] **Step 5: Commit**

```bash
git add src/task_queue.py tests/test_task_queue.py
git commit -m "feat(queue): enqueue accepts branch_name and mutates_repo"
```


================================================================================
END FILE: phase-c-queue.md
================================================================================


================================================================================
BEGIN FILE: phase-d-relay.md
================================================================================

# Phase D — Relay stamps `task_id` on outbound emails

One task. Wires `msg.task_id` through `relay_outbound_messages` to `record_outbound_email`. The schema column (Phase A.2), the method signature (Phase A.3), and the `messages.task_id` source (already populated by `chat_notify` / `chat_ask` callers) are all in place — this task connects them.

Phase A.3's `DO UPDATE / COALESCE` semantics ensure that if `set_email_message_id` ran first (recording the row with task_id=NULL), the relay's later call with the real `task_id` will fill it in.

---

## Task D.7: `relay_outbound_messages` passes `task_id` through

**Files:**
- Modify: `src/chat_relay.py:112-118`
- Modify: `tests/test_chat_relay.py` (add one test class)

`send_threaded_reply` in `chat_handlers.py` is the inbound-ACK path — those messages have no originating task, so its `record_outbound_email` call stays as-is (defaults `task_id` to None).

- [ ] **Step 1: Read existing fixtures**

Run: `.venv/bin/pytest tests/test_chat_relay.py --collect-only -q | head -20`

Inspect `tests/test_chat_relay.py` to find the existing `config` fixture (the SMTP credentials dict every relay test uses). Reuse it in the new test instead of redefining.

- [ ] **Step 2: Write the failing test**

Append to `tests/test_chat_relay.py`:

```python
class TestRelayStampsTaskId:
    """An agent's chat_notify carries msg.task_id through the bus; the
    relay must persist it on outbound_emails so a user reply on this
    thread can be walked back to the originating task (Phase F)."""

    def test_relay_passes_task_id_to_outbound_table(
        self, tmp_path, mocker, config,
    ):
        from src.chat_db import ChatDB
        from src.chat_relay import relay_outbound_messages

        cdb = ChatDB(str(tmp_path / "db"))
        cdb.register_agent("agent-p", str(tmp_path))
        # Seed a task so _should_relay treats the message as email-origin.
        cdb._conn.execute(
            "INSERT INTO tasks (id, project_path, body, created_at, "
            "origin_message_id, origin_from) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (777, str(tmp_path), "x", "2026-05-17T00:00:00+00:00",
             "<orig@x>", "user@example.org"),
        )
        cdb._conn.commit()
        cdb.insert_message(
            "agent-p", "user", "result body", "notify", task_id=777,
        )
        mocker.patch(
            "src.chat_relay.send_reply", return_value="<sent-id@x>",
        )
        relay_outbound_messages(config, cdb)

        row = cdb.find_outbound_email("<sent-id@x>")
        assert row is not None
        assert row["task_id"] == 777

    def test_relay_without_task_id_records_null(
        self, tmp_path, mocker, config,
    ):
        """ask messages from a CLI-only agent (no task) still relay (ask
        always relays) but their outbound row has task_id=NULL."""
        from src.chat_db import ChatDB
        from src.chat_relay import relay_outbound_messages

        cdb = ChatDB(str(tmp_path / "db2"))
        cdb.register_agent("agent-q", str(tmp_path))
        cdb.insert_message(
            "agent-q", "user", "should I continue?", "ask",
        )  # no task_id
        mocker.patch(
            "src.chat_relay.send_reply", return_value="<sent-q@x>",
        )
        relay_outbound_messages(config, cdb)

        row = cdb.find_outbound_email("<sent-q@x>")
        assert row is not None
        assert row["task_id"] is None
```

If `tests/test_chat_relay.py` lacks a module-level `config` fixture, copy the dict from the nearest existing relay test (the file uses one — check the top of the existing `TestRelayOutbound` class).

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_chat_relay.py::TestRelayStampsTaskId -v`
Expected: `test_relay_passes_task_id_to_outbound_table` FAILs on `assert row["task_id"] == 777` (currently NULL).

- [ ] **Step 4: Pass `task_id` in `relay_outbound_messages`**

Edit `src/chat_relay.py:112-118`:

```python
        if email_msg_id:
            chat_db.set_email_message_id(msg["id"], email_msg_id)
            chat_db.record_outbound_email(
                email_msg_id,
                kind=msg.get("type") or "notify",
                sender_agent=msg["from_name"],
                task_id=msg.get("task_id"),
            )
```

That's the entire change. `msg["task_id"]` is already in the row dict because `messages.task_id` is in the schema (`src/chat_schema.py:28`) and `insert_message` accepts the kwarg (`src/chat_db.py:52-71`).

- [ ] **Step 5: Run tests**

```
.venv/bin/pytest tests/test_chat_relay.py -v
.venv/bin/pytest tests/ -q
```

Expected: all PASS. (Exact count varies; capture in Phase H.14.)

- [ ] **Step 6: Commit**

```bash
git add src/chat_relay.py tests/test_chat_relay.py
git commit -m "feat(relay): stamp task_id on outbound emails for reply-walkback"
```


================================================================================
END FILE: phase-d-relay.md
================================================================================


================================================================================
BEGIN FILE: phase-e-branch-prep.md
================================================================================

# Phase E — Branch preparation (the central behavioral change)

Two tasks. The matrix here is where v2 diverges most from v1.

- **E.8** Pure move: extract the current `_prepare_branch` body from `project_worker.py` into a new `src/branch_prep.py`. No behavior change yet.
- **E.9** Implement the nine-cell matrix using `current_branch == prior` as the safe-to-continue axis. Add `branch_exists`, `checkout_existing_branch`, `is_valid_task_branch` to `git_ops`.

The matrix solves both reviewer blockers 1 and 2:

| `is_git_repo` | `mutates_repo` | `prior_branch` | `on prior?` | `is_clean` | Action |
|---|---|---|---|---|---|
| no | any | any | any | any | run, no branch |
| yes | False | none | — | any | run, skip dirty check, no branch |
| yes | True/NULL | none | — | clean | new branch, run |
| yes | True/NULL | none | — | dirty | **fail** |
| yes | any | set | yes | any | run on this branch |
| yes | any | set | no | clean | checkout prior, run |
| yes | any | set | no | dirty | **fail** |
| yes | any | set-missing | — | clean | fresh new branch |
| yes | any | set-missing | — | dirty | **fail** |

---

## Task E.8: Extract `branch_prep` (move-only)

**Files:**
- Create: `src/branch_prep.py`
- Modify: `src/project_worker.py:23-25` (drop `git_ops` imports the worker no longer uses; import `prepare_branch`)
- Modify: `src/project_worker.py:69` (call site)
- Delete: `src/project_worker.py:123-146` (the inline `_prepare_branch`)
- Test: `tests/test_branch_prep.py` (create; matrix tests come in E.9)
- Modify: `tests/test_project_worker.py:15-20` (move autouse patch target)

- [ ] **Step 1: Write the failing extraction tests**

`tests/test_branch_prep.py`:

```python
"""Tests for src/branch_prep.py — extracted from project_worker.

Task E.8 (this file) pins the extraction. Task E.9 grows this file
with the full nine-cell matrix."""
from src import branch_prep, project_worker


def test_branch_prep_module_exists():
    assert hasattr(branch_prep, "prepare_branch")


def test_project_worker_delegates_to_branch_prep(mocker, tmp_path):
    """The worker's run_task must call src.branch_prep.prepare_branch
    rather than the deleted inline _prepare_branch. Pinning the
    indirection guards against a future merge resurrecting the old
    helper."""
    sentinel = mocker.patch(
        "src.project_worker.prepare_branch", return_value=False,
    )
    queue = mocker.MagicMock()
    queue.get.return_value = {"id": 1}
    claimed = {"id": 1, "body": "x", "branch_name": None, "mutates_repo": None}
    cfg = project_worker.WorkerConfig(
        project_path=str(tmp_path), db_path=str(tmp_path / "db"),
        claude_bin="claude", mcp_config=str(tmp_path / ".mcp.json"),
    )
    project_worker.run_task(queue, claimed, cfg)
    sentinel.assert_called_once()
```

- [ ] **Step 2: Run test to verify failures**

Run: `.venv/bin/pytest tests/test_branch_prep.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Create `src/branch_prep.py` (verbatim extraction)**

```python
"""Per-task branch preparation — extracted from project_worker.

Lives in its own module so the worker stays under 200 lines and the
nine-cell matrix has room for focused tests.

Behavior in this revision is byte-identical to the deleted inline
_prepare_branch. Task E.9 in the implementation plan adds the
mutates_repo + branch_name + current_branch matrix."""
import logging

from src.git_ops import (
    checkout_new_branch, is_clean, is_git_repo, task_branch_name,
)

logger = logging.getLogger(__name__)


def prepare_branch(queue, task: dict, project_path: str) -> bool:
    """Create a per-task branch. Returns False if the task was marked failed.

    Non-git projects skip silently. Dirty repos refuse — protects the
    user's uncommitted work."""
    tid = task["id"]
    body = task["body"]
    if not is_git_repo(project_path):
        logger.info(
            "worker task %d: %s is not a git repo — running without branch",
            tid, project_path,
        )
        return True
    clean, status = is_clean(project_path)
    if not clean:
        msg = f"repo dirty — commit or stash first:\n{status}"
        queue.mark_failed(tid, msg)
        logger.warning("worker task %d: %s", tid, msg)
        return False
    branch = task_branch_name(tid, body)
    ok, err = checkout_new_branch(project_path, branch)
    if not ok:
        queue.mark_failed(tid, f"could not create branch {branch}: {err}")
        logger.warning("worker task %d: checkout failed: %s", tid, err)
        return False
    queue.set_branch(tid, branch)
    logger.info("worker task %d: on branch %s", tid, branch)
    return True
```

- [ ] **Step 4: Wire `project_worker.py`**

Replace `src/project_worker.py:23-29` imports:

```python
from src.branch_prep import prepare_branch
from src.task_log import log_task_finished
from src.task_notifier import notify_task_done
from src.task_queue import TaskQueue
```

Replace the call site in `run_task` (`src/project_worker.py:69`):

```python
    if not prepare_branch(queue, claimed, cfg.project_path):
        _finish(queue, tid, cfg)
        return
```

Delete the entire inline `_prepare_branch` (lines 123-146).

- [ ] **Step 5: Move the autouse-patch target in `tests/test_project_worker.py`**

The fixture at `tests/test_project_worker.py:15-20` currently patches `src.project_worker.is_git_repo`. After this move, that symbol is no longer in `project_worker` (the import is gone). Update:

```python
@pytest.fixture(autouse=True)
def _skip_branch_prep(mocker):
    """Default: treat project_path as non-git so run_task skips branch work.
    Tests that exercise the branch dance override src.branch_prep.is_git_repo
    themselves."""
    mocker.patch("src.branch_prep.is_git_repo", return_value=False)
```

- [ ] **Step 6: Run tests + line check**

```
.venv/bin/pytest tests/test_branch_prep.py tests/test_project_worker.py -v
.venv/bin/pytest tests/ -q
scripts/check-line-limit.sh
```

Expected: all PASS; no file >200 lines. (Exact count varies; capture in Phase H.14.)

- [ ] **Step 7: Commit**

```bash
git add src/branch_prep.py src/project_worker.py tests/test_branch_prep.py tests/test_project_worker.py
git commit -m "refactor(worker): extract prepare_branch into src/branch_prep.py"
```

---

## Task E.9: Implement the nine-cell matrix

**Files:**
- Modify: `src/git_ops.py` (add three helpers)
- Modify: `src/branch_prep.py` (replace body with matrix)
- Modify: `tests/test_git_ops.py` (cover the new helpers)
- Modify: `tests/test_branch_prep.py` (full matrix)

### Step 1: Add helpers to `src/git_ops.py` (plus detached-HEAD fix for `current_branch`)

- [ ] **Step 1a: Failing tests for the new git_ops helpers + detached-HEAD test**

Append to `tests/test_git_ops.py`:

```python
class TestBranchExists:
    def test_returns_true_for_existing_branch(self, tmp_path):
        from src.git_ops import branch_exists
        _init_repo_with_branch(tmp_path, "feature/x")
        assert branch_exists(str(tmp_path), "feature/x") is True

    def test_returns_false_for_missing_branch(self, tmp_path):
        from src.git_ops import branch_exists
        _init_repo(tmp_path)
        assert branch_exists(str(tmp_path), "nonexistent") is False


class TestCheckoutExistingBranch:
    def test_switches_to_existing(self, tmp_path):
        from src.git_ops import checkout_existing_branch, current_branch
        _init_repo_with_branch(tmp_path, "feature/x")
        ok, err = checkout_existing_branch(str(tmp_path), "feature/x")
        assert ok is True and err == ""
        assert current_branch(str(tmp_path)) == "feature/x"

    def test_returns_error_for_missing(self, tmp_path):
        from src.git_ops import checkout_existing_branch
        _init_repo(tmp_path)
        ok, err = checkout_existing_branch(str(tmp_path), "nope")
        assert ok is False
        assert err  # non-empty stderr


class TestIsValidTaskBranch:
    @pytest.mark.parametrize("name", [
        "claude/task-1-foo",
        "claude/task-42-also-add-docs",
        "claude/task-9999-some-long-slug-here",
    ])
    def test_valid(self, name):
        from src.git_ops import is_valid_task_branch
        assert is_valid_task_branch(name) is True

    @pytest.mark.parametrize("name", [
        "",
        "main",
        "feature/x",
        "claude/task-",
        "claude/task-abc-foo",
        "../escape",
        "claude/task-1; rm -rf /",
    ])
    def test_invalid(self, name):
        from src.git_ops import is_valid_task_branch
        assert is_valid_task_branch(name) is False


class TestCurrentBranchDetachedHEAD:
    """Round-3 reviewer catch: `git rev-parse --abbrev-ref HEAD` prints
    the literal string 'HEAD' when detached, NOT an empty string. The
    matrix in branch_prep relies on `current == prior` for the safe-to-
    continue cell, so an un-normalized 'HEAD' return would compare
    incorrectly. Real-git test, not just a mocked return."""

    def test_returns_branch_name_on_branch(self, tmp_path):
        from src.git_ops import current_branch
        _init_repo(tmp_path)
        assert current_branch(str(tmp_path)) == "main"

    def test_returns_empty_string_on_detached_head(self, tmp_path):
        from src.git_ops import current_branch
        import subprocess as sp
        _init_repo(tmp_path)
        # Capture HEAD sha, then detach to it
        sha = sp.run(
            ["git", "rev-parse", "HEAD"], cwd=tmp_path,
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        sp.run(
            ["git", "checkout", "--detach", sha], cwd=tmp_path,
            check=True, capture_output=True,
        )
        assert current_branch(str(tmp_path)) == ""

    def test_returns_empty_string_outside_repo(self, tmp_path):
        from src.git_ops import current_branch
        # tmp_path is not a git repo
        assert current_branch(str(tmp_path)) == ""
```

Add helper module-level fixtures at the top of `tests/test_git_ops.py` if not present:

```python
def _git_env():
    import os
    return {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x",
    }


def _init_repo(path):
    import subprocess as sp
    sp.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    sp.run(["git", "commit", "--allow-empty", "-m", "init", "--no-gpg-sign"],
           cwd=path, check=True, env=_git_env())


def _init_repo_with_branch(path, branch):
    import subprocess as sp
    _init_repo(path)
    sp.run(["git", "branch", branch], cwd=path, check=True)
```

(If `tests/test_git_ops.py` already has equivalents, reuse them.)

- [ ] **Step 1b: Run tests — should fail**

Run: `.venv/bin/pytest tests/test_git_ops.py::TestBranchExists tests/test_git_ops.py::TestIsValidTaskBranch -v`
Expected: FAIL — symbols don't exist yet.

- [ ] **Step 1c: Add helpers + normalize `current_branch` in `src/git_ops.py`**

First, replace `current_branch` (currently at `src/git_ops.py:32-34`):

```python
def current_branch(path: str) -> str:
    """Return the current branch name, or "" when not on a named branch.

    `git rev-parse --abbrev-ref HEAD` prints the literal string "HEAD"
    when the repo is in detached-HEAD state; the matrix in
    src.branch_prep relies on `current == prior` to detect the safe-to-
    continue cell, so we normalize to "" for detached HEAD (the same
    sentinel we use for not-a-git-repo)."""
    rc, out, _ = _git(["rev-parse", "--abbrev-ref", "HEAD"], path)
    if rc != 0 or out == "HEAD":
        return ""
    return out
```

Then insert before `checkout_new_branch`:

```python
def branch_exists(path: str, branch_name: str) -> bool:
    """Local-branch existence check. Remote-only branches return False —
    we don't auto-fetch from a follow-up task; that's a user choice."""
    rc, _, _ = _git(
        ["show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"],
        path,
    )
    return rc == 0


def checkout_existing_branch(path: str, branch_name: str) -> tuple[bool, str]:
    """Switch to an existing branch. Returns (success, error_text)."""
    rc, _, err = _git(["checkout", branch_name], path)
    return (rc == 0, err if rc != 0 else "")
```

At the top of the file (or near `_SLUG_RE`), add:

```python
_VALID_TASK_BRANCH_RE = re.compile(r"^claude/task-\d+-[a-z0-9-]+$")


def is_valid_task_branch(name: str) -> bool:
    """Defense-in-depth: only reuse branch names that match our schema."""
    return bool(_VALID_TASK_BRANCH_RE.match(name or ""))
```

- [ ] **Step 1d: Run tests**

Run: `.venv/bin/pytest tests/test_git_ops.py -v`
Expected: all PASS.

### Step 2: Replace `src/branch_prep.py` body with the matrix

- [ ] **Step 2a: Write the failing matrix tests**

Replace the contents of `tests/test_branch_prep.py` (keeping the two existing pin tests at the top):

```python
"""Tests for src/branch_prep.py — the nine-cell matrix.

Decision order:
  1. Not a git repo                              → no branch, succeed.
  2. mutates_repo == False AND no prior branch   → no dirty check, no branch.
  3. Has a (valid) prior branch:
       a. currently on prior branch              → run (allow dirty).
       b. not on prior + clean + branch exists   → checkout existing.
       c. not on prior + clean + branch missing  → fresh new branch.
       d. not on prior + dirty                   → fail (can't switch).
  4. Mutating, no prior branch, clean            → new branch.
  5. Mutating, no prior branch, dirty            → fail.
"""
import pytest

from src import branch_prep, project_worker


@pytest.fixture
def queue(mocker):
    q = mocker.MagicMock()
    q.mark_failed = mocker.MagicMock()
    q.set_branch = mocker.MagicMock()
    return q


def _task(tid=1, body="do X", branch_name=None, mutates_repo=None):
    return {
        "id": tid, "body": body,
        "branch_name": branch_name, "mutates_repo": mutates_repo,
    }


def test_branch_prep_module_exists():
    assert hasattr(branch_prep, "prepare_branch")


def test_project_worker_delegates_to_branch_prep(mocker, tmp_path):
    sentinel = mocker.patch(
        "src.project_worker.prepare_branch", return_value=False,
    )
    q = mocker.MagicMock()
    q.get.return_value = {"id": 1}
    claimed = _task()
    cfg = project_worker.WorkerConfig(
        project_path=str(tmp_path), db_path=str(tmp_path / "db"),
        claude_bin="claude", mcp_config=str(tmp_path / ".mcp.json"),
    )
    project_worker.run_task(q, claimed, cfg)
    sentinel.assert_called_once()


class TestNonGit:
    def test_skips_branch_work(self, queue, mocker):
        mocker.patch("src.branch_prep.is_git_repo", return_value=False)
        assert branch_prep.prepare_branch(queue, _task(), "/tmp") is True
        queue.set_branch.assert_not_called()
        queue.mark_failed.assert_not_called()


class TestReadOnlyNoPrior:
    def test_skips_dirty_check_and_branch(self, queue, mocker):
        mocker.patch("src.branch_prep.is_git_repo", return_value=True)
        is_clean = mocker.patch("src.branch_prep.is_clean")
        co_new = mocker.patch("src.branch_prep.checkout_new_branch")
        assert (
            branch_prep.prepare_branch(
                queue, _task(mutates_repo=False), "/tmp",
            )
            is True
        )
        is_clean.assert_not_called()
        co_new.assert_not_called()
        queue.set_branch.assert_not_called()


class TestPriorBranchAlreadyOn:
    """The safe-to-continue cell. Allow dirty (this is OUR work)."""

    @pytest.mark.parametrize("mutates", [True, False, None])
    def test_runs_even_if_dirty(self, queue, mocker, mutates):
        mocker.patch("src.branch_prep.is_git_repo", return_value=True)
        mocker.patch(
            "src.branch_prep.current_branch",
            return_value="claude/task-17-fix",
        )
        is_clean = mocker.patch(
            "src.branch_prep.is_clean", return_value=(False, " M x"),
        )
        co_new = mocker.patch("src.branch_prep.checkout_new_branch")
        co_existing = mocker.patch("src.branch_prep.checkout_existing_branch")
        ok = branch_prep.prepare_branch(
            queue,
            _task(branch_name="claude/task-17-fix", mutates_repo=mutates),
            "/tmp",
        )
        assert ok is True
        # No checkout calls — we're already on the right branch.
        co_new.assert_not_called()
        co_existing.assert_not_called()
        # is_clean may or may not have been called; the contract is just
        # "don't fail and don't switch."
        queue.mark_failed.assert_not_called()


class TestPriorBranchCheckout:
    """Clean repo, not on prior branch, branch exists → checkout."""

    def test_clean_and_exists_checks_out(self, queue, mocker):
        mocker.patch("src.branch_prep.is_git_repo", return_value=True)
        mocker.patch(
            "src.branch_prep.current_branch", return_value="main",
        )
        mocker.patch("src.branch_prep.is_clean", return_value=(True, ""))
        mocker.patch("src.branch_prep.branch_exists", return_value=True)
        co = mocker.patch(
            "src.branch_prep.checkout_existing_branch", return_value=(True, ""),
        )
        co_new = mocker.patch("src.branch_prep.checkout_new_branch")
        ok = branch_prep.prepare_branch(
            queue,
            _task(branch_name="claude/task-17-fix", mutates_repo=True),
            "/tmp",
        )
        assert ok is True
        co.assert_called_once_with("/tmp", "claude/task-17-fix")
        co_new.assert_not_called()


class TestPriorBranchMissingFallback:
    """Clean repo, not on prior branch, branch GONE → fresh new branch."""

    def test_missing_branch_creates_fresh(self, queue, mocker):
        mocker.patch("src.branch_prep.is_git_repo", return_value=True)
        mocker.patch(
            "src.branch_prep.current_branch", return_value="main",
        )
        mocker.patch("src.branch_prep.is_clean", return_value=(True, ""))
        mocker.patch("src.branch_prep.branch_exists", return_value=False)
        co_new = mocker.patch(
            "src.branch_prep.checkout_new_branch", return_value=(True, ""),
        )
        ok = branch_prep.prepare_branch(
            queue,
            _task(tid=42, body="follow up",
                  branch_name="claude/task-17-gone", mutates_repo=True),
            "/tmp",
        )
        assert ok is True
        co_new.assert_called_once()
        queue.set_branch.assert_called_once()
        new_branch = queue.set_branch.call_args.args[1]
        assert new_branch.startswith("claude/task-42-")


class TestPriorBranchDirtySwitch:
    """Dirty repo, not on prior branch → fail. Cannot switch safely."""

    @pytest.mark.parametrize("mutates", [True, False, None])
    def test_dirty_switch_fails(self, queue, mocker, mutates):
        mocker.patch("src.branch_prep.is_git_repo", return_value=True)
        mocker.patch(
            "src.branch_prep.current_branch", return_value="main",
        )
        mocker.patch(
            "src.branch_prep.is_clean", return_value=(False, " M x.py"),
        )
        co = mocker.patch("src.branch_prep.checkout_existing_branch")
        ok = branch_prep.prepare_branch(
            queue,
            _task(branch_name="claude/task-17-fix", mutates_repo=mutates),
            "/tmp",
        )
        assert ok is False
        queue.mark_failed.assert_called_once()
        co.assert_not_called()


class TestNewBranchPath:
    def test_mutating_clean_no_prior_creates_new(self, queue, mocker):
        mocker.patch("src.branch_prep.is_git_repo", return_value=True)
        mocker.patch("src.branch_prep.is_clean", return_value=(True, ""))
        co = mocker.patch(
            "src.branch_prep.checkout_new_branch", return_value=(True, ""),
        )
        ok = branch_prep.prepare_branch(
            queue,
            _task(tid=99, body="implement X", mutates_repo=None),
            "/tmp",
        )
        assert ok is True
        co.assert_called_once()
        queue.set_branch.assert_called_once()

    def test_mutating_dirty_no_prior_fails(self, queue, mocker):
        mocker.patch("src.branch_prep.is_git_repo", return_value=True)
        mocker.patch(
            "src.branch_prep.is_clean", return_value=(False, " M x.py"),
        )
        ok = branch_prep.prepare_branch(
            queue, _task(mutates_repo=None), "/tmp",
        )
        assert ok is False
        queue.mark_failed.assert_called_once()

    def test_new_branch_checkout_failure_marks_failed(self, queue, mocker):
        mocker.patch("src.branch_prep.is_git_repo", return_value=True)
        mocker.patch("src.branch_prep.is_clean", return_value=(True, ""))
        mocker.patch(
            "src.branch_prep.checkout_new_branch",
            return_value=(False, "branch already exists"),
        )
        ok = branch_prep.prepare_branch(queue, _task(), "/tmp")
        assert ok is False
        queue.mark_failed.assert_called_once()


class TestInvalidPriorBranchName:
    """Reviewer defense-in-depth: a bad row in tasks.branch_name is
    treated as no prior branch — fall through to normal logic."""

    def test_invalid_prior_name_falls_through(self, queue, mocker):
        mocker.patch("src.branch_prep.is_git_repo", return_value=True)
        mocker.patch("src.branch_prep.is_clean", return_value=(True, ""))
        co_existing = mocker.patch("src.branch_prep.checkout_existing_branch")
        co_new = mocker.patch(
            "src.branch_prep.checkout_new_branch", return_value=(True, ""),
        )
        ok = branch_prep.prepare_branch(
            queue,
            _task(branch_name="../escape", mutates_repo=True),
            "/tmp",
        )
        assert ok is True
        co_existing.assert_not_called()
        co_new.assert_called_once()


class TestDetachedHEAD:
    """current_branch returns '' on detached HEAD. Treat as 'not on
    prior' and fall through to the clean/dirty checks."""

    def test_detached_clean_checks_out_prior(self, queue, mocker):
        mocker.patch("src.branch_prep.is_git_repo", return_value=True)
        mocker.patch("src.branch_prep.current_branch", return_value="")
        mocker.patch("src.branch_prep.is_clean", return_value=(True, ""))
        mocker.patch("src.branch_prep.branch_exists", return_value=True)
        co = mocker.patch(
            "src.branch_prep.checkout_existing_branch", return_value=(True, ""),
        )
        ok = branch_prep.prepare_branch(
            queue,
            _task(branch_name="claude/task-1-foo", mutates_repo=True),
            "/tmp",
        )
        assert ok is True
        co.assert_called_once()
```

- [ ] **Step 2b: Run — should fail**

Run: `.venv/bin/pytest tests/test_branch_prep.py -v`
Expected: most new tests FAIL.

- [ ] **Step 2c: Implement the matrix in `src/branch_prep.py`**

Replace the entire body:

```python
"""Per-task branch preparation — the nine-cell matrix.

Decisions (in order, return as soon as one fires):
  1. Not a git repo                              → no branch, succeed.
  2. mutates_repo == False AND no valid prior    → skip dirty check + no
                                                    branch, succeed.
  3. Has a valid prior branch:
       3a. already on prior                      → succeed (allow dirty,
                                                    this is OUR work).
       3b. not on prior + dirty                  → fail (can't switch).
       3c. not on prior + clean + branch exists  → checkout existing.
       3d. not on prior + clean + branch missing → fresh new branch.
  4. Mutating, no valid prior, repo clean        → new branch.
  5. Mutating, no valid prior, repo dirty        → fail.

The mutates_repo column may be NULL (unknown). NULL is treated as
mutating so existing rows and ambiguous-classifier rows stay
safety-gated.
"""
import logging

from src.git_ops import (
    branch_exists, checkout_existing_branch, checkout_new_branch,
    current_branch, is_clean, is_git_repo, is_valid_task_branch,
    task_branch_name,
)

logger = logging.getLogger(__name__)


def _is_mutating(task: dict) -> bool:
    """NULL or 1 → mutating; only an explicit 0/False is read-only."""
    v = task.get("mutates_repo")
    if v is None:
        return True
    return bool(v)


def _valid_prior(task: dict) -> str:
    """Return the prior branch_name if it's set AND valid; else ''."""
    name = (task.get("branch_name") or "").strip()
    if name and is_valid_task_branch(name):
        return name
    if name:
        logger.warning("ignoring invalid prior branch_name: %r", name)
    return ""


def prepare_branch(queue, task: dict, project_path: str) -> bool:
    tid = task["id"]

    if not is_git_repo(project_path):
        logger.info(
            "worker task %d: %s is not a git repo — running without branch",
            tid, project_path,
        )
        return True

    prior = _valid_prior(task)

    if not prior and not _is_mutating(task):
        logger.info(
            "worker task %d: read-only, no prior branch — skipping dirty "
            "check and branch creation", tid,
        )
        return True

    if prior:
        return _handle_prior(queue, task, project_path, prior)

    return _new_branch(queue, task, project_path)


def _handle_prior(
    queue, task: dict, project_path: str, prior: str,
) -> bool:
    tid = task["id"]
    current = current_branch(project_path)
    if current == prior:
        logger.info(
            "worker task %d: already on prior branch %s — continuing",
            tid, prior,
        )
        return True

    clean, status = is_clean(project_path)
    if not clean:
        msg = (
            f"repo dirty on '{current or 'detached HEAD'}', cannot switch "
            f"to prior branch '{prior}' safely; commit or stash first:\n"
            f"{status}"
        )
        queue.mark_failed(tid, msg)
        logger.warning("worker task %d: %s", tid, msg)
        return False

    if not branch_exists(project_path, prior):
        logger.info(
            "worker task %d: prior branch %s missing — creating fresh",
            tid, prior,
        )
        return _new_branch(queue, task, project_path)

    ok, err = checkout_existing_branch(project_path, prior)
    if not ok:
        queue.mark_failed(
            tid, f"could not checkout existing branch {prior}: {err}",
        )
        logger.warning(
            "worker task %d: checkout existing failed: %s", tid, err,
        )
        return False
    logger.info("worker task %d: reusing branch %s", tid, prior)
    return True


def _new_branch(queue, task: dict, project_path: str) -> bool:
    tid = task["id"]
    clean, status = is_clean(project_path)
    if not clean:
        msg = f"repo dirty — commit or stash first:\n{status}"
        queue.mark_failed(tid, msg)
        logger.warning("worker task %d: %s", tid, msg)
        return False
    branch = task_branch_name(tid, task["body"])
    ok, err = checkout_new_branch(project_path, branch)
    if not ok:
        queue.mark_failed(tid, f"could not create branch {branch}: {err}")
        logger.warning("worker task %d: checkout failed: %s", tid, err)
        return False
    queue.set_branch(tid, branch)
    logger.info("worker task %d: on branch %s", tid, branch)
    return True
```

Key implementation notes for the implementer:
- `current_branch` is imported from `git_ops` and returns `""` on detached HEAD. The `current == prior` check treats `""` as "not on prior" (always false), so detached HEAD falls through to the clean/dirty logic. Test `TestDetachedHEAD::test_detached_clean_checks_out_prior` pins this.
- `_handle_prior` checks `is_clean` *before* `branch_exists`. The order matters: if dirty + branch missing, we still fail (the user has unrelated work — don't silently create a fresh branch over their changes). If clean + branch missing, fall to `_new_branch`.
- `set_branch` is only called when `_new_branch` actually creates a new branch. Reusing a prior branch leaves `branch_name` as-is in the row (which it already is — that's how we found the prior).

- [ ] **Step 3: Run all relevant tests + line check**

```
.venv/bin/pytest tests/test_branch_prep.py tests/test_git_ops.py tests/test_project_worker.py -v
.venv/bin/pytest tests/ -q
scripts/check-line-limit.sh
```

Expected: all PASS; no file >200 lines. (Exact count varies; capture in Phase H.14.)

- [ ] **Step 4: Commit**

```bash
git add src/branch_prep.py src/git_ops.py tests/test_branch_prep.py tests/test_git_ops.py
git commit -m "feat(worker): branch_prep nine-cell matrix with current-branch axis"
```


================================================================================
END FILE: phase-e-branch-prep.md
================================================================================


================================================================================
BEGIN FILE: phase-f-reply-routing.md
================================================================================

# Phase F — Reply routing with branch reuse and honest ACK

One task. Folds three reviewer corrections into `apply_reply`:

1. **Project/agent mismatch guard** in `_prior_branch` — verify the prior task lives in the same project and the outbound row's sender_agent matches the reply's agent_name, so a misrouted reply can't inherit a branch from another project.
2. **Outcome-accurate ACK text** — three sentences, picked based on whether we reused a prior branch / queued read-only / planned a new branch.
3. **Pass `In-Reply-To` from `chat_handlers._handle_reply`** so `apply_reply` has the header to walk back.

The classifier (Phase B.4) and the queue extension (Phase C.6) are the prerequisites.

---

## Task F.10: `apply_reply` walks outbound → prior task → branch (with guards)

**Files:**
- Modify: `src/reply_router.py` (extend imports + `apply_reply` body)
- Modify: `src/chat_handlers.py:115-130` (thread `In-Reply-To` through)
- Modify: `tests/test_reply_router.py` (adapt `_FakeTaskQueue`)
- Create: `tests/test_apply_reply_branch_reuse.py`

### Lookup chain

```
inbound message
  → In-Reply-To header
  → chat_db.find_outbound_email(header)
  → outbound.task_id present?
  → outbound.sender_agent == agent_name? (strict eq, NULL fails)  [guard 1]
  → task_queue.get(outbound.task_id)
  → prior.project_path == decision.project_path?  [guard 2]
  → prior.branch_name
```

Any failed link → no prior branch → caller falls back to today's "fresh branch" behavior. No exceptions, no warnings — this is a best-effort enrichment.

**Round-3 reviewer note on guard 1:** strict equality (not "set-and-different"). A row with `task_id` set but `sender_agent=NULL` must NOT inherit the prior branch — fail closed. `task_id` is a new column so legitimate old rows shouldn't have it, but if one slips through with NULL sender we still refuse to inherit.

### ACK selection

```python
if prior_branch:
    "Queued as task #N for AGENT to continue prior branch `B` (worker pid P)."
elif mutates is False:
    "Queued as task #N for AGENT as a read-only task (no branch will be created; worker pid P)."
else:
    "Queued as task #N for AGENT on planned branch `B` (worker pid P)."
```

The middle sentence is new — v1's ACK lied for this case.

**Round-3 reviewer note on wording:** "continue prior branch" instead of "existing branch". The prior branch can be deleted between enqueue and worker run; the matrix in Phase E.9 falls through to a fresh new branch in that case, so the ACK would lie if it promised "existing". "Continue prior" stays accurate either way without duplicating matrix logic in `apply_reply` (which would also have a TOCTOU window).

### Steps

- [ ] **Step 1: Write the failing tests**

`tests/test_apply_reply_branch_reuse.py`:

```python
"""Tests for the branch-reuse + guards path in src/reply_router.apply_reply.

Lookup chain: In-Reply-To → outbound_emails.task_id → tasks row →
branch_name + mutates_repo. Guards: project_path must match, and
outbound.sender_agent must match agent_name."""
import pytest

from src.chat_db import ChatDB
from src.reply_router import apply_reply
from src.task_queue import TaskQueue


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "db")
    ChatDB(path)
    return path


@pytest.fixture
def db(db_path):
    return ChatDB(db_path)


@pytest.fixture
def tq(db_path):
    return TaskQueue(db_path)


class _StubWM:
    def __init__(self, pid=111):
        self.pid = pid

    def ensure_worker(self, _path):
        return self.pid


def _project_dir(tmp_path, name="p"):
    p = tmp_path / name
    p.mkdir()
    return str(p.resolve())


def _seed_prior_task(
    db, tq, project_path, branch_name, agent_name="agent-p", mutating=True,
):
    """Insert a completed prior task + a relayed outbound email pointing
    to it. Returns (task_id, outbound Message-ID)."""
    tid = tq.enqueue(
        project_path, "implement X",
        branch_name=branch_name,
        mutates_repo=mutating,
        origin_message_id="<orig@x>",
        origin_from="user@example.org",
    )
    tq.mark_done(tid)
    db.insert_message(agent_name, "user", "done", "notify", task_id=tid)
    out_id = f"<sent-{tid}@x>"
    db.record_outbound_email(
        out_id, kind="result", sender_agent=agent_name, task_id=tid,
    )
    return tid, out_id


def _latest(tq):
    """Return the most recently inserted task across all projects."""
    row = tq._conn.execute(
        "SELECT * FROM tasks ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return dict(row)


class TestBranchReuseFromOutbound:
    def test_reuses_prior_branch_for_mutating_followup(
        self, db, tq, tmp_path,
    ):
        proj = _project_dir(tmp_path)
        db.register_agent("agent-p", proj)
        prior_id, out_id = _seed_prior_task(
            db, tq, proj, "claude/task-17-fix-bus", mutating=True,
        )
        original = db.insert_message(
            "agent-p", "user", "done", "notify", task_id=prior_id,
        )
        ack, _tag = apply_reply(
            db, tq, _StubWM(pid=222),
            agent_name="agent-p", original_message_id=original["id"],
            body="also add docs",
            allowed_base=str(tmp_path),
            original_email_message_id=out_id,
        )
        new = _latest(tq)
        assert new["branch_name"] == "claude/task-17-fix-bus"
        assert new["mutates_repo"] == 1
        # ACK reflects reuse (round-3 wording change)
        assert "continue prior branch" in ack
        assert "claude/task-17-fix-bus" in ack

    def test_read_only_followup_after_mutating_task_reuses_branch(
        self, db, tq, tmp_path,
    ):
        """Reviewer's specific case: 'explain what you changed' must
        reuse the prior branch so the worker runs in the right tree."""
        proj = _project_dir(tmp_path)
        db.register_agent("agent-p", proj)
        prior_id, out_id = _seed_prior_task(
            db, tq, proj, "claude/task-17-fix-bus", mutating=True,
        )
        original = db.insert_message(
            "agent-p", "user", "done", "notify", task_id=prior_id,
        )
        apply_reply(
            db, tq, _StubWM(),
            agent_name="agent-p", original_message_id=original["id"],
            body="explain what you changed",
            allowed_base=str(tmp_path),
            original_email_message_id=out_id,
        )
        new = _latest(tq)
        assert new["branch_name"] == "claude/task-17-fix-bus"  # reused
        assert new["mutates_repo"] == 0  # classified read-only

    def test_no_outbound_match_falls_through(self, db, tq, tmp_path):
        """Pre-deploy outbound rows have no task_id. Reply still queues
        with no branch_name → worker creates fresh."""
        proj = _project_dir(tmp_path)
        db.register_agent("agent-p", proj)
        original = db.insert_message("agent-p", "user", "done", "notify")
        ack, _tag = apply_reply(
            db, tq, _StubWM(),
            agent_name="agent-p", original_message_id=original["id"],
            body="add docs",
            allowed_base=str(tmp_path),
            original_email_message_id="<never-sent@x>",
        )
        new = _latest(tq)
        assert new["branch_name"] is None
        # ACK reflects planned branch
        assert "planned branch" in ack


class TestGuards:
    def test_null_sender_agent_rejects_prior(self, db, tq, tmp_path):
        """Round-3 reviewer fix: strict equality. A row with task_id
        but sender_agent=NULL must NOT inherit the prior branch."""
        proj = _project_dir(tmp_path)
        db.register_agent("agent-p", proj)
        prior_id = tq.enqueue(
            proj, "implement X",
            branch_name="claude/task-9-foo", mutates_repo=True,
            origin_message_id="<orig@x>", origin_from="user@example.org",
        )
        tq.mark_done(prior_id)
        # Insert outbound row directly with NULL sender_agent
        db._conn.execute(
            "INSERT INTO outbound_emails "
            "(email_message_id, sent_at, kind, sender_agent, task_id) "
            "VALUES (?, ?, ?, NULL, ?)",
            ("<no-sender@x>", "2026-05-17T00:00:00+00:00", "result", prior_id),
        )
        db._conn.commit()
        original = db.insert_message("agent-p", "user", "done", "notify")
        apply_reply(
            db, tq, _StubWM(),
            agent_name="agent-p", original_message_id=original["id"],
            body="follow up",
            allowed_base=str(tmp_path),
            original_email_message_id="<no-sender@x>",
        )
        assert _latest(tq)["branch_name"] is None  # guard fired

    def test_project_mismatch_rejects_prior(self, db, tq, tmp_path):
        """If outbound's prior task lives in project A but the reply
        routes to agent B in project B, do NOT inherit A's branch."""
        proj_a = _project_dir(tmp_path, "a")
        proj_b = _project_dir(tmp_path, "b")
        db.register_agent("agent-a", proj_a)
        db.register_agent("agent-b", proj_b)
        # Prior task in project A
        prior_id, out_id = _seed_prior_task(
            db, tq, proj_a, "claude/task-1-thing-in-a",
            agent_name="agent-a", mutating=True,
        )
        # Reply routes to agent-b
        original = db.insert_message("agent-b", "user", "done", "notify")
        apply_reply(
            db, tq, _StubWM(),
            agent_name="agent-b", original_message_id=original["id"],
            body="follow up",
            allowed_base=str(tmp_path),
            original_email_message_id=out_id,
        )
        new = _latest(tq)
        assert new["project_path"] == proj_b
        assert new["branch_name"] is None  # guard fired

    def test_agent_mismatch_rejects_prior(self, db, tq, tmp_path):
        """outbound.sender_agent must equal agent_name."""
        proj = _project_dir(tmp_path)
        db.register_agent("agent-p", proj)
        db.register_agent("agent-other", proj)
        prior_id, out_id = _seed_prior_task(
            db, tq, proj, "claude/task-9-foo",
            agent_name="agent-other", mutating=True,
        )
        original = db.insert_message("agent-p", "user", "done", "notify")
        apply_reply(
            db, tq, _StubWM(),
            agent_name="agent-p", original_message_id=original["id"],
            body="follow up",
            allowed_base=str(tmp_path),
            original_email_message_id=out_id,
        )
        new = _latest(tq)
        assert new["branch_name"] is None  # guard fired


class TestClassifierIntegration:
    def test_mutating_body_stamps_true(self, db, tq, tmp_path):
        proj = _project_dir(tmp_path)
        db.register_agent("agent-p", proj)
        original = db.insert_message("agent-p", "user", "x", "notify")
        apply_reply(
            db, tq, _StubWM(),
            agent_name="agent-p", original_message_id=original["id"],
            body="fix the bus",
            allowed_base=str(tmp_path),
            original_email_message_id="",
        )
        assert _latest(tq)["mutates_repo"] == 1

    def test_read_only_body_stamps_false(self, db, tq, tmp_path):
        proj = _project_dir(tmp_path)
        db.register_agent("agent-p", proj)
        original = db.insert_message("agent-p", "user", "x", "notify")
        apply_reply(
            db, tq, _StubWM(),
            agent_name="agent-p", original_message_id=original["id"],
            body="explain the relay",
            allowed_base=str(tmp_path),
            original_email_message_id="",
        )
        new = _latest(tq)
        assert new["mutates_repo"] == 0
        # Read-only ACK
        # (note: empty body would be None, but "explain..." is non-empty)

    def test_empty_body_leaves_mutates_null(self, db, tq, tmp_path):
        proj = _project_dir(tmp_path)
        db.register_agent("agent-p", proj)
        original = db.insert_message("agent-p", "user", "x", "notify")
        apply_reply(
            db, tq, _StubWM(),
            agent_name="agent-p", original_message_id=original["id"],
            body="",
            allowed_base=str(tmp_path),
            original_email_message_id="",
        )
        assert _latest(tq)["mutates_repo"] is None


class TestAckText:
    def test_read_only_ack_says_no_branch(self, db, tq, tmp_path):
        proj = _project_dir(tmp_path)
        db.register_agent("agent-p", proj)
        original = db.insert_message("agent-p", "user", "x", "notify")
        ack, _tag = apply_reply(
            db, tq, _StubWM(),
            agent_name="agent-p", original_message_id=original["id"],
            body="show me the schema",
            allowed_base=str(tmp_path),
            original_email_message_id="",
        )
        assert "read-only" in ack.lower()
        assert "no branch" in ack.lower()

    def test_planned_branch_ack_for_new_mutating_task(
        self, db, tq, tmp_path,
    ):
        proj = _project_dir(tmp_path)
        db.register_agent("agent-p", proj)
        original = db.insert_message("agent-p", "user", "x", "notify")
        ack, _tag = apply_reply(
            db, tq, _StubWM(),
            agent_name="agent-p", original_message_id=original["id"],
            body="implement the new endpoint",
            allowed_base=str(tmp_path),
            original_email_message_id="",
        )
        assert "planned branch" in ack
        assert "claude/task-" in ack
```

- [ ] **Step 2: Run tests — should fail**

Run: `.venv/bin/pytest tests/test_apply_reply_branch_reuse.py -v`
Expected: FAIL — `apply_reply()` has no `original_email_message_id` kwarg.

- [ ] **Step 3: Replace `src/reply_router.py`**

Full rewrite:

```python
"""Reply sub-classification + branch-reuse for email follow-ups.

Three routes (unchanged):
- reply_to_ask: original was a chat_ask → goes on the bus so the
  blocking chat_ask returns.
- reply_to_project: agent has a valid project_path under CLAUDE_CWD →
  queue the reply body as a task and ensure a worker is running.
- reply_bus_only: neither of the above → fall back to bus-only.

Branch-reuse layer: when the user replies on a thread we sent for a
task, walk In-Reply-To → outbound_emails.task_id → prior task to get
the prior branch_name. Guards: prior task must be in the same project,
and outbound.sender_agent must match agent_name. mutates_repo is
classified from the reply body so read-only follow-ups skip the dirty
check (Phase E's matrix).
"""
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from src.git_ops import task_branch_name
from src.mutation_classifier import classify_mutation

logger = logging.getLogger(__name__)


@dataclass
class ReplyDecision:
    route: str   # "ask" | "project" | "bus"
    project_path: str = ""
    ack_subject_suffix: str = ""


def classify_reply(
    chat_db, agent_name: str, original_message_id: int, allowed_base: str,
) -> ReplyDecision:
    original = chat_db.get_message(original_message_id)
    if original is not None and original.get("type") == "ask":
        return ReplyDecision(route="ask")
    agent = chat_db.get_agent(agent_name)
    project_path = (agent or {}).get("project_path", "")
    if project_path and _project_in_base(project_path, allowed_base):
        return ReplyDecision(
            route="project",
            project_path=str(Path(project_path).resolve()),
        )
    return ReplyDecision(route="bus")


def _project_in_base(project_path: str, allowed_base: str) -> bool:
    if not allowed_base or not project_path:
        return False
    try:
        base = str(Path(allowed_base).resolve())
        resolved = str(Path(project_path).resolve())
    except OSError:
        return False
    if not os.path.isdir(resolved):
        return False
    return resolved == base or resolved.startswith(base + os.sep)


def _prior_branch(
    chat_db, task_queue, in_reply_to_header: str,
    project_path: str, agent_name: str,
) -> str:
    """Walk inbound In-Reply-To → outbound_emails.task_id → tasks.branch_name.

    Returns "" when any link is missing OR when sender_agent doesn't
    strictly equal agent_name (NULL fails too — fail closed) OR when
    the prior task is in a different project. Defense against misrouted
    replies inheriting the wrong branch."""
    if not in_reply_to_header or task_queue is None:
        return ""
    outbound = chat_db.find_outbound_email(in_reply_to_header)
    if not outbound or not outbound.get("task_id"):
        return ""
    if outbound.get("sender_agent") != agent_name:
        logger.info(
            "ignoring prior task: outbound sender_agent=%r != reply agent=%r",
            outbound.get("sender_agent"), agent_name,
        )
        return ""
    prior = task_queue.get(outbound["task_id"])
    if not prior:
        return ""
    if prior.get("project_path") != project_path:
        logger.info(
            "ignoring prior task: project mismatch (prior=%s, reply=%s)",
            prior.get("project_path"), project_path,
        )
        return ""
    return (prior.get("branch_name") or "")


def _format_ack(
    *, task_id: int, agent_name: str, worker_pid: int,
    prior_branch: str, mutates: bool | None, body: str,
) -> tuple[str, str]:
    """Return (ack_body, subject_tag). One of three sentences, chosen
    by actual outcome so the ACK never lies about whether a branch will
    exist.

    'continue prior branch' (not 'existing branch') is the round-3
    wording fix: the matrix in src.branch_prep may fall back to a fresh
    new branch if the prior was deleted between enqueue and worker run,
    and 'continue prior' stays accurate either way."""
    tag = f"Queued #{task_id}"
    if prior_branch:
        body_text = (
            f"Queued as task #{task_id} for {agent_name} to continue prior "
            f"branch `{prior_branch}` (worker pid {worker_pid})."
        )
    elif mutates is False:
        body_text = (
            f"Queued as task #{task_id} for {agent_name} as a read-only task "
            f"(no branch will be created; worker pid {worker_pid})."
        )
    else:
        branch = task_branch_name(task_id, body)
        body_text = (
            f"Queued as task #{task_id} for {agent_name} on planned branch "
            f"`{branch}` (worker pid {worker_pid})."
        )
    return body_text, tag


def apply_reply(
    chat_db, task_queue, worker_manager, *,
    agent_name: str, original_message_id: int,
    body: str, allowed_base: str,
    original_email_message_id: str = "",
) -> tuple[str, str]:
    """Record the reply and act on it. Returns (ack_body, subject_tag)."""
    decision = classify_reply(chat_db, agent_name, original_message_id, allowed_base)
    chat_db.insert_message(
        "user", agent_name, body, "reply", in_reply_to=original_message_id,
    )
    if decision.route == "project" and task_queue and worker_manager:
        prior_branch = _prior_branch(
            chat_db, task_queue, original_email_message_id,
            decision.project_path, agent_name,
        )
        mutates = classify_mutation(body)
        try:
            worker_pid = worker_manager.ensure_worker(decision.project_path)
            task_id = task_queue.enqueue(
                decision.project_path, body,
                branch_name=prior_branch,
                mutates_repo=mutates,
            )
        except ValueError as exc:
            logger.warning("Reply enqueue failed: %s", exc)
            return (
                f"Delivered to {agent_name} on the chat bus (couldn't queue: {exc}).",
                "Delivered",
            )
        return _format_ack(
            task_id=task_id, agent_name=agent_name, worker_pid=worker_pid,
            prior_branch=prior_branch, mutates=mutates, body=body,
        )
    if decision.route == "ask":
        return (
            f"Answer delivered to {agent_name} (was waiting on a question).",
            "Answer",
        )
    return (f"Delivered to {agent_name} on the chat bus.", "Delivered")
```

- [ ] **Step 4: Thread `In-Reply-To` through `chat_handlers._handle_reply`**

Edit `src/chat_handlers.py:115-130`:

```python
def _handle_reply(
    route, message, config: dict, chat_db: ChatDB,
    task_queue: TaskQueue | None, worker_manager: WorkerManager | None,
) -> None:
    body = extract_command(message, strip_secret=config.get("shared_secret", ""))
    ack, tag = apply_reply(
        chat_db, task_queue, worker_manager,
        agent_name=route.agent_name,
        original_message_id=route.original_message_id,
        body=body, allowed_base=config.get("claude_cwd") or "",
        original_email_message_id=message.get("In-Reply-To", "").strip(),
    )
    logger.info("Reply routed: %s", ack)
    send_threaded_reply(
        config, message, ack, tag=tag, chat_db=chat_db, kind="reply_ack",
        sender_agent=route.agent_name,
    )
```

- [ ] **Step 5: Adapt `tests/test_reply_router.py` fakes**

The existing `_FakeTaskQueue` accepts `enqueue(self, path, body, priority=0)` — that breaks when `apply_reply` passes `branch_name=` and `mutates_repo=`. Update:

```python
class _FakeTaskQueue:
    def __init__(self):
        self.enqueued = []

    def enqueue(self, path, body, priority=0, branch_name="", mutates_repo=None,
                **_):
        self.enqueued.append((path, body, priority, branch_name, mutates_repo))
        return 42

    def get(self, _task_id):
        return None  # no prior task in the legacy fixtures
```

Adjust the existing tuple-equality assertion in `test_project_reply_enqueues_and_acks`. The body is `"also add docs"`; `classify_mutation("also add docs")` returns True (because `add` is in `_MUTATING`). The tuple shape grows to 5 fields:

```python
        assert tq.enqueued == [
            (proj, "also add docs", 0, "", True),
        ]
```

The ACK assertion in the same test (`"#42" in ack and "555" in ack` + `"claude/task-42-also-add-docs" in ack`) still holds because the planned-branch ACK includes the planned name. The "planned branch" prefix changes the wording but `claude/task-42-also-add-docs` is still in the string.

If `test_project_reply_enqueues_and_acks` also asserts a specific phrasing of "Queued as task #42 for agent-p on branch ...", relax it to check the substring `claude/task-42-also-add-docs` only — the new ACK uses "planned branch" instead of "branch".

- [ ] **Step 6: Run tests**

```
.venv/bin/pytest tests/test_reply_router.py tests/test_apply_reply_branch_reuse.py -v
.venv/bin/pytest tests/ -q
scripts/check-line-limit.sh
```

Expected: all PASS; `src/reply_router.py` under 200 lines. (Exact count varies; capture in Phase H.14.)

- [ ] **Step 7: Commit**

```bash
git add src/reply_router.py src/chat_handlers.py tests/test_reply_router.py tests/test_apply_reply_branch_reuse.py
git commit -m "feat(reply): walk outbound→prior task; project/agent guard; honest ACK"
```


================================================================================
END FILE: phase-f-reply-routing.md
================================================================================


================================================================================
BEGIN FILE: phase-g-enqueue-tool.md
================================================================================

# Phase G — `enqueue_task_tool` auto-classifies; `retry_task_tool` inherits intent

Two sub-tasks now (round-3 reviewer non-blocker folded in):

- **G.11a** `enqueue_task_tool` auto-classifies + accurate `planned_branch`. Closes v2 reviewer blocker 3.
- **G.11b** `retry_task_tool` inherits `mutates_repo` AND `branch_name` from the original task so a retry of a read-only task stays read-only and continues on the same branch instead of forking a fresh one.

Same fix as Phase F.10 for the ACK side: the returned `planned_branch` is now empty (or omitted) when the task is read-only, so MCP callers don't see a fake branch name.

The JSON envelope path (`src/json_handler/*`) routes through `enqueue_task_tool` already, so it picks up classification for free — confirmed by reading `src/json_handler/*` imports.

---

## Task G.11a: `enqueue_task_tool` classifies + accurate `planned_branch`

**Files:**
- Modify: `chat/project_tools.py:41-70`
- Modify: `tests/test_enqueue_task_tool.py` (add 4 cases)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_enqueue_task_tool.py`:

```python
class TestMutatesRepoHint:
    def test_explicit_false_persists(self, tq, mgr, tmp_path, mocker):
        (tmp_path / "p").mkdir()
        proc = mocker.MagicMock(pid=1)
        proc.poll.return_value = None
        mocker.patch("src.worker_manager.subprocess.Popen", return_value=proc)
        result = enqueue_task_tool(
            tq, mgr, project="p", body="show me the schema",
            allowed_base=str(tmp_path),
            mutates_repo=False,
        )
        assert tq.get(result["task_id"])["mutates_repo"] == 0

    def test_default_auto_classifies_read_only(
        self, tq, mgr, tmp_path, mocker,
    ):
        """v2 reviewer blocker 3: first-time tasks must classify too,
        not just replies. 'explain' is read-only."""
        (tmp_path / "p").mkdir()
        proc = mocker.MagicMock(pid=1)
        proc.poll.return_value = None
        mocker.patch("src.worker_manager.subprocess.Popen", return_value=proc)
        result = enqueue_task_tool(
            tq, mgr, project="p", body="explain the schema",
            allowed_base=str(tmp_path),
        )
        assert tq.get(result["task_id"])["mutates_repo"] == 0

    def test_default_auto_classifies_mutating(
        self, tq, mgr, tmp_path, mocker,
    ):
        (tmp_path / "p").mkdir()
        proc = mocker.MagicMock(pid=1)
        proc.poll.return_value = None
        mocker.patch("src.worker_manager.subprocess.Popen", return_value=proc)
        result = enqueue_task_tool(
            tq, mgr, project="p", body="fix the relay",
            allowed_base=str(tmp_path),
        )
        assert tq.get(result["task_id"])["mutates_repo"] == 1

    def test_empty_body_stays_null(self, tq, mgr, tmp_path, mocker):
        (tmp_path / "p").mkdir()
        proc = mocker.MagicMock(pid=1)
        proc.poll.return_value = None
        mocker.patch("src.worker_manager.subprocess.Popen", return_value=proc)
        result = enqueue_task_tool(
            tq, mgr, project="p", body="",
            allowed_base=str(tmp_path),
        )
        assert tq.get(result["task_id"])["mutates_repo"] is None

    def test_explicit_hint_overrides_classifier(
        self, tq, mgr, tmp_path, mocker,
    ):
        """Caller's explicit hint wins. 'fix the bus' classifies as
        mutating, but mutates_repo=False from the caller stands."""
        (tmp_path / "p").mkdir()
        proc = mocker.MagicMock(pid=1)
        proc.poll.return_value = None
        mocker.patch("src.worker_manager.subprocess.Popen", return_value=proc)
        result = enqueue_task_tool(
            tq, mgr, project="p", body="fix the bus",
            allowed_base=str(tmp_path),
            mutates_repo=False,
        )
        assert tq.get(result["task_id"])["mutates_repo"] == 0


class TestPlannedBranchHonesty:
    """The planned_branch field must be empty for read-only tasks since
    branch_prep will not create a branch in that case."""

    def test_read_only_returns_empty_planned_branch(
        self, tq, mgr, tmp_path, mocker,
    ):
        (tmp_path / "p").mkdir()
        proc = mocker.MagicMock(pid=1)
        proc.poll.return_value = None
        mocker.patch("src.worker_manager.subprocess.Popen", return_value=proc)
        result = enqueue_task_tool(
            tq, mgr, project="p", body="explain the schema",
            allowed_base=str(tmp_path),
        )
        # Either omitted or empty string — caller should treat both as
        # 'no branch will be created'.
        assert not result.get("planned_branch")

    def test_mutating_returns_real_planned_branch(
        self, tq, mgr, tmp_path, mocker,
    ):
        (tmp_path / "p").mkdir()
        proc = mocker.MagicMock(pid=1)
        proc.poll.return_value = None
        mocker.patch("src.worker_manager.subprocess.Popen", return_value=proc)
        result = enqueue_task_tool(
            tq, mgr, project="p", body="implement X",
            allowed_base=str(tmp_path),
        )
        assert result["planned_branch"].startswith("claude/task-")
        assert result["planned_branch"].endswith("implement-x")
```

- [ ] **Step 2: Run tests — should fail**

Run: `.venv/bin/pytest tests/test_enqueue_task_tool.py::TestMutatesRepoHint -v`
Expected: FAIL on the keyword arg.

- [ ] **Step 3: Extend `enqueue_task_tool`**

Replace `chat/project_tools.py:41-70`:

```python
def enqueue_task_tool(
    queue: TaskQueue, manager: WorkerManager, *,
    project: str, body: str, priority: int = 0,
    allowed_base: str, plan_first: bool = False,
    origin_content_type: str = "", origin_message_id: str = "",
    origin_subject: str = "", origin_from: str = "",
    dispatch_token: str = "", origin_envelope_v: int | None = None,
    mutates_repo: bool | None = None,
) -> dict:
    try:
        resolved = resolve_project(project, allowed_base)
    except ValueError as exc:
        return error_result_from_exc(exc)
    try:
        worker_pid = manager.ensure_worker(resolved)
    except ValueError as exc:
        return error_result_from_exc(exc)
    # Auto-classify when caller didn't pass a hint. Explicit hints win.
    if mutates_repo is None:
        mutates_repo = classify_mutation(body)
    task_id = queue.enqueue(
        resolved, body, priority=_clamp_priority(priority), plan_first=plan_first,
        origin_content_type=origin_content_type,
        origin_message_id=origin_message_id, origin_subject=origin_subject,
        origin_from=origin_from, dispatch_token=dispatch_token,
        origin_envelope_v=origin_envelope_v,
        mutates_repo=mutates_repo,
    )
    # planned_branch is what branch_prep WOULD create — for read-only
    # tasks no branch will be created, so omit (don't lie).
    planned_branch = (
        "" if mutates_repo is False else task_branch_name(task_id, body)
    )
    return {
        "status": "enqueued",
        "task_id": task_id,
        "worker_pid": worker_pid,
        "planned_branch": planned_branch,
        "plan_first": plan_first,
    }
```

Add the classifier import at the top of `chat/project_tools.py:8-18`:

```python
from src.error_codes import (
    ProjectNotFound, ProjectOutsideBase, error_result_from_exc,
)
from src.git_ops import task_branch_name
from src.mutation_classifier import classify_mutation
from chat.project_helpers import last_activity
from src.task_control import cancel_running_task, queue_status
from src.task_queue import TaskQueue
from src.worker_manager import WorkerManager
```

- [ ] **Step 4: Verify the existing happy-path test still passes**

The existing `test_happy_path_spawns_worker_and_returns_ids` asserts:

```python
assert result["planned_branch"].startswith("claude/task-")
assert result["planned_branch"].endswith("write-tests")
```

`"write tests"` → `classify_mutation` returns True (`write` in `_MUTATING`), so `planned_branch` is non-empty. The existing assertion still holds.

- [ ] **Step 5: Run tests**

```
.venv/bin/pytest tests/test_enqueue_task_tool.py -v
.venv/bin/pytest tests/ -q
scripts/check-line-limit.sh
```

Expected: all PASS; `chat/project_tools.py` under 200. (Exact count varies; capture in Phase H.14.)

- [ ] **Step 6: Commit**

```bash
git add chat/project_tools.py tests/test_enqueue_task_tool.py
git commit -m "feat(mcp): enqueue_task_tool auto-classifies; planned_branch is honest"
```

---

## Task G.11b: `retry_task_tool` inherits `mutates_repo` + `branch_name`

**Files:**
- Modify: `chat/project_tools.py:97-129` (the `retry_task_tool` body)
- Modify: `tests/test_retry_task_tool.py` (add 3 cases)

A retry is "do this same thing again, presumably the first attempt failed." The retry should inherit the original's classification and branch:

- **mutates_repo**: a retry of a read-only task is also read-only; a retry of a mutating task is mutating. Inheriting avoids running the classifier again on the same body (which would also be fine, but inheriting is cheaper + preserves any manual hint the original was given).
- **branch_name**: if the original ran on `claude/task-17-fix`, the retry should continue on that branch. Otherwise the retry forks pointlessly.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_retry_task_tool.py`:

```python
class TestRetryInheritsIntent:
    """Round-3 reviewer fix: retries inherit mutates_repo + branch_name
    so a retry of a read-only task stays read-only and continues on the
    same branch instead of forking a fresh one."""

    def test_retry_inherits_mutates_repo_false(
        self, tq, mgr, tmp_path, mocker,
    ):
        (tmp_path / "p").mkdir()
        proc = mocker.MagicMock(pid=1)
        proc.poll.return_value = None
        mocker.patch("src.worker_manager.subprocess.Popen", return_value=proc)
        original_id = tq.enqueue(
            str((tmp_path / "p").resolve()), "show me the schema",
            mutates_repo=False,
        )
        tq.mark_failed(original_id, "test")
        result = retry_task_tool(tq, mgr, task_id=original_id)
        new = tq.get(result["new_task_id"])
        assert new["mutates_repo"] == 0

    def test_retry_inherits_mutates_repo_true(
        self, tq, mgr, tmp_path, mocker,
    ):
        (tmp_path / "p").mkdir()
        proc = mocker.MagicMock(pid=1)
        proc.poll.return_value = None
        mocker.patch("src.worker_manager.subprocess.Popen", return_value=proc)
        original_id = tq.enqueue(
            str((tmp_path / "p").resolve()), "fix it",
            mutates_repo=True,
        )
        tq.mark_failed(original_id, "test")
        result = retry_task_tool(tq, mgr, task_id=original_id)
        assert tq.get(result["new_task_id"])["mutates_repo"] == 1

    def test_retry_inherits_branch_name(self, tq, mgr, tmp_path, mocker):
        (tmp_path / "p").mkdir()
        proc = mocker.MagicMock(pid=1)
        proc.poll.return_value = None
        mocker.patch("src.worker_manager.subprocess.Popen", return_value=proc)
        original_id = tq.enqueue(
            str((tmp_path / "p").resolve()), "fix it",
            branch_name="claude/task-9-existing", mutates_repo=True,
        )
        tq.mark_failed(original_id, "test")
        result = retry_task_tool(tq, mgr, task_id=original_id)
        new = tq.get(result["new_task_id"])
        assert new["branch_name"] == "claude/task-9-existing"

    def test_retry_inherits_null_mutates_repo(
        self, tq, mgr, tmp_path, mocker,
    ):
        """Pre-existing rows have NULL mutates_repo — inheriting NULL
        keeps them safety-gated (today's behavior)."""
        (tmp_path / "p").mkdir()
        proc = mocker.MagicMock(pid=1)
        proc.poll.return_value = None
        mocker.patch("src.worker_manager.subprocess.Popen", return_value=proc)
        original_id = tq.enqueue(
            str((tmp_path / "p").resolve()), "ambiguous",
        )  # mutates_repo defaults to None → NULL
        tq.mark_failed(original_id, "test")
        result = retry_task_tool(tq, mgr, task_id=original_id)
        assert tq.get(result["new_task_id"])["mutates_repo"] is None
```

- [ ] **Step 2: Run tests — should fail**

Run: `.venv/bin/pytest tests/test_retry_task_tool.py::TestRetryInheritsIntent -v`
Expected: FAIL — retry currently passes neither `mutates_repo` nor `branch_name`, so both fields are NULL on the new row regardless of the original.

- [ ] **Step 3: Patch `retry_task_tool`**

Replace `chat/project_tools.py:119-123` (the `queue.enqueue(...)` call inside `retry_task_tool`):

```python
    new_id = queue.enqueue(
        project_path, body,
        priority=_clamp_priority(original.get("priority") or 0),
        retry_of=task_id,
        branch_name=original.get("branch_name") or "",
        mutates_repo=(
            None if original.get("mutates_repo") is None
            else bool(original.get("mutates_repo"))
        ),
    )
```

The `None if … is None else bool(...)` dance handles the disk-format (NULL/0/1) → Python-format (None/False/True) conversion correctly. Without it, `bool(0)` would be `False` (correct), `bool(1)` `True` (correct), but `bool(None)` is `False` (WRONG — would silently flip NULL to read-only).

- [ ] **Step 4: Run tests**

```
.venv/bin/pytest tests/test_retry_task_tool.py -v
.venv/bin/pytest tests/ -q
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add chat/project_tools.py tests/test_retry_task_tool.py
git commit -m "feat(mcp): retry_task_tool inherits mutates_repo and branch_name"
```


================================================================================
END FILE: phase-g-enqueue-tool.md
================================================================================


================================================================================
BEGIN FILE: phase-h-finalize.md
================================================================================

# Phase H — Finalize: concurrency invariant, docs, simplify, verify

Three tasks. None changes production code (except possibly the `/simplify` sweep).

- **H.12** Pin the one-running-task-per-project invariant that branch reuse depends on.
- **H.13** Update `README.md` + `website/index.html` + `website/fa/index.html` (always in lockstep per CLAUDE.md).
- **H.14** `/simplify` sweep + coverage check + final verification + DB-migration smoke + commit story.

---

## Task H.12: Fix `claim_next` then pin the one-running-per-project invariant (TDD)

**Round-3 reviewer blocker 1:** `claim_next` does NOT currently enforce one-running-per-project — it only filters on `project_path` + `status='pending'`. Two back-to-back calls without `mark_done` in between would claim two rows simultaneously. The "one worker per project" property of today's system comes from the *worker model* (one worker process per project, calls `claim_next` once per loop iteration, runs to completion). The queue itself is permissive.

Branch reuse makes this property load-bearing in a way it wasn't before — if a second worker ever slips through (e.g. via `worker_manager` race), two tasks could run concurrently on the same branch and corrupt each other. v2's H.12 was framed as a regression-pin test that would have passed today; v2.1 reframes it as a fix-then-test (TDD) task: the test fails today, we fix `claim_next` with a `NOT EXISTS` clause, then it passes.

The ghost reaper (`src/ghost_reaper.py`) already cleans stale `running` rows whose pid is dead, so the new `NOT EXISTS` guard is safe — it won't deadlock against a crashed worker's row.

**Files:**
- Modify: `src/task_queue.py:96-108` (claim_next SQL + params)
- Create: `tests/test_one_running_per_branch.py`

- [ ] **Step 1: Write the failing test**

```python
"""claim_next must enforce one running task per project so branch
reuse can never end up with two concurrent workers on the same branch.

Round-3 reviewer blocker 1: today's claim_next does NOT enforce this
— two consecutive claim_next calls without an intervening mark_done
will claim two rows. This file pins the new invariant added by the
NOT EXISTS guard in src/task_queue.py."""
from src.chat_db import ChatDB
from src.task_queue import TaskQueue


def test_claim_next_yields_one_at_a_time(tmp_path):
    path = str(tmp_path / "db")
    ChatDB(path)
    tq = TaskQueue(path)
    a = tq.enqueue("/p", "task a", branch_name="claude/task-1-foo")
    b = tq.enqueue("/p", "task b", branch_name="claude/task-1-foo")

    first = tq.claim_next("/p")
    assert first["id"] == a
    # Second claim returns None while first is still 'running'.
    assert tq.claim_next("/p") is None
    # Once first finishes, second becomes claimable.
    tq.mark_done(first["id"])
    second = tq.claim_next("/p")
    assert second["id"] == b


def test_second_claim_returns_none_even_with_priority(tmp_path):
    """A higher-priority pending task does NOT preempt a running one —
    the NOT EXISTS guard fires before priority ordering."""
    path = str(tmp_path / "db")
    ChatDB(path)
    tq = TaskQueue(path)
    tq.enqueue("/p", "low", priority=0)
    tq.claim_next("/p")  # 'low' is now running
    tq.enqueue("/p", "urgent", priority=10)
    assert tq.claim_next("/p") is None


def test_running_task_is_singleton_per_project(tmp_path):
    path = str(tmp_path / "db")
    ChatDB(path)
    tq = TaskQueue(path)
    tq.enqueue("/p", "a")
    tq.enqueue("/p", "b")
    tq.enqueue("/p", "c")
    tq.claim_next("/p")
    running = tq._conn.execute(
        "SELECT * FROM tasks WHERE project_path='/p' AND status='running'"
    ).fetchall()
    assert len(running) == 1


def test_two_projects_can_run_concurrently(tmp_path):
    """Per-project workers can run in parallel — only intra-project
    serialization is the invariant. The NOT EXISTS guard is scoped to
    project_path so a /p1 claim does NOT block a /p2 claim."""
    path = str(tmp_path / "db")
    ChatDB(path)
    tq = TaskQueue(path)
    tq.enqueue("/p1", "x")
    tq.enqueue("/p2", "y")
    assert tq.claim_next("/p1") is not None
    assert tq.claim_next("/p2") is not None  # not blocked by /p1's claim


def test_failed_task_does_not_block_next_claim(tmp_path):
    """A 'failed' row is not 'running', so it doesn't trip the NOT
    EXISTS guard."""
    path = str(tmp_path / "db")
    ChatDB(path)
    tq = TaskQueue(path)
    a = tq.enqueue("/p", "a")
    b = tq.enqueue("/p", "b")
    tq.claim_next("/p")  # a → running
    tq.mark_failed(a, "boom")  # a → failed
    second = tq.claim_next("/p")
    assert second is not None
    assert second["id"] == b
```

- [ ] **Step 2: Run tests — should fail**

Run: `.venv/bin/pytest tests/test_one_running_per_branch.py -v`
Expected: `test_claim_next_yields_one_at_a_time`, `test_second_claim_returns_none_even_with_priority`, and `test_running_task_is_singleton_per_project` FAIL — today's `claim_next` happily claims a second row while the first is running.

- [ ] **Step 3: Patch `claim_next` in `src/task_queue.py`**

Replace `src/task_queue.py:96-108`:

```python
    def claim_next(self, project_path: str) -> dict | None:
        """Atomically move the oldest pending task for a project to running.

        The NOT EXISTS clause enforces one-running-task-per-project at
        the queue layer (not just at the worker layer), so branch reuse
        can never produce two concurrent workers on the same branch
        even if a worker_manager race spawns a second worker. The ghost
        reaper handles stale 'running' rows from crashed workers, so
        this guard never deadlocks."""
        cur = self._conn.execute(
            "UPDATE tasks SET status='running', started_at=? "
            "WHERE id=(SELECT id FROM tasks "
            "          WHERE project_path=? AND status='pending' "
            "          ORDER BY priority DESC, id ASC LIMIT 1) "
            "AND status='pending' "
            "AND NOT EXISTS ("
            "    SELECT 1 FROM tasks r "
            "    WHERE r.project_path=? AND r.status='running'"
            ") "
            "RETURNING *",
            (_now(), project_path, project_path),
        )
        row = cur.fetchone()
        self._conn.commit()
        return public_row(dict(row)) if row else None
```

Two changes from before:
1. Added the `AND NOT EXISTS (SELECT 1 FROM tasks r WHERE r.project_path=? AND r.status='running')` clause.
2. Added a third `?` parameter (`project_path` again) to the tuple.

- [ ] **Step 4: Run all tests**

```
.venv/bin/pytest tests/test_one_running_per_branch.py tests/test_task_queue.py -v
.venv/bin/pytest tests/ -q
```

Expected: new tests PASS; existing `tests/test_task_queue.py::TestClaimNext` cases still pass (they don't exercise the new behavior). Final test count varies — capture in H.14.

If `tests/test_task_queue.py::TestClaimNext::test_claim_next_ignores_non_pending` breaks because it now interacts with the guard differently, read its setup carefully — it enqueues ONE task, claims it, and expects the next claim to return None. That still passes: only one task exists, so the next claim has no pending row to choose from regardless of the running guard.

- [ ] **Step 5: Commit**

```bash
git add src/task_queue.py tests/test_one_running_per_branch.py
git commit -m "fix(queue): claim_next enforces one running task per project (NOT EXISTS guard)"
```

---

## Task H.13: Docs sweep

Per CLAUDE.md: "Docs follow code — whenever a change alters user-visible behavior, configuration surface, or the test count, update README.md and the website (website/index.html, website/fa/index.html in lockstep) in the same PR."

**Files:**
- Modify: `README.md`
- Modify: `website/index.html`
- Modify: `website/fa/index.html`

- [ ] **Step 1: Update `README.md`**

Find the user-facing behavior section. Add a short subsection (≤8 lines):

```markdown
### Read-only tasks skip the dirty-repo gate

Tasks classified as obviously read-only (`explain …`, `show …`, `list …`,
plain interrogatives, polite forms like `can you explain …`) no longer
require a clean working tree and no longer fork a per-task branch.
Mutating tasks still require clean for a fresh branch. Classification is
conservative — anything ambiguous (e.g. `also fix the rest`) is treated
as mutating. The classifier is regex-only and runs server-side.

### Email follow-ups continue on the same branch

When you reply on a thread that came from a prior task's result, the
follow-up task reuses the prior task's branch — even if the prior task
left it dirty (uncommitted edits from the previous turn are treated as
*your* work in progress). If the repo has unrelated dirty changes on a
different branch, the follow-up still fails with a clear "cannot switch
safely" message. The lookup walks the SMTP `In-Reply-To` header back
through `outbound_emails.task_id`; pre-existing rows without that
column behave exactly as today.
```

Bump the test-count badge if present. New count is whatever `.venv/bin/pytest tests/ -q` reports — do not assume a specific number; read it from the test runner output before committing.

- [ ] **Step 2: Update `website/index.html` and `website/fa/index.html`**

Apply the same two subsections in both files. Match the existing structural pattern (CSS sections) — don't introduce new section types unless asked. The Farsi (`fa`) version needs translation; if the file shows machine-translation patterns, mirror that style.

- [ ] **Step 3: Run the full suite to confirm nothing regressed**

```
.venv/bin/pytest tests/ -q
```

Expected: identical to Task H.12.

- [ ] **Step 4: Commit**

```bash
git add README.md website/index.html website/fa/index.html
git commit -m "docs: dirty-gate skip + branch reuse for follow-ups"
```

---

## Task H.14: `/simplify` + coverage + final verification

### Step 1: `/simplify` sweep

Per memory `feedback_simplify_when_done`: `/simplify` the working tree before commit and fold fixups into the same commit (no separate fixup commits).

- [ ] **Step 1a: Invoke `simplify`**

Use the `simplify` skill — pass the list of files touched in this PR:

```
src/outbound_emails_store.py
src/chat_db.py
src/chat_schema.py
src/mutation_classifier.py
src/task_row_redact.py
src/task_queue.py
src/chat_relay.py
src/branch_prep.py
src/git_ops.py
src/project_worker.py
src/reply_router.py
src/chat_handlers.py
chat/project_tools.py
```

- [ ] **Step 1b: Re-run the full suite + line check**

```
.venv/bin/pytest tests/ -q
scripts/check-line-limit.sh
```

Expected: same count; all files ≤200 lines.

- [ ] **Step 1c: Commit (only if `/simplify` actually changed something)**

```bash
git add -p   # review every hunk
git commit -m "style: post-simplify cleanup"
```

If `/simplify` found nothing, skip.

### Step 2: Coverage check

- [ ] **Step 2a: Run pytest with coverage**

```
.venv/bin/pytest tests/ --cov=src --cov=chat --cov-report=term-missing
```

Expected: 100% on production code. The `.coveragerc` omits tests, the entry shim, and pragma patterns.

- [ ] **Step 2b: Patch any uncovered lines**

Most likely uncovered:
- `src/branch_prep.py` — the warning branch in `_valid_prior` for invalid names. Add a test that exercises it (the existing `TestInvalidPriorBranchName` covers the function return; the log call also runs).
- `src/mutation_classifier.py` — the `s == prefix` branch in `_strip_polite` (body that is exactly a polite prefix with no trailing word). Add `assert classify_mutation("please") is None` (after strip it's empty → None).
- `src/reply_router.py` — the `OSError` branch in `_project_in_base`. Existing test `test_path_resolve_oserror_classified_as_bus` covers it.

Add tests for any actually-uncovered lines, commit:

```bash
git add tests/
git commit -m "test: lift coverage back to 100% on touched modules"
```

### Step 3: DB migration smoke

- [ ] **Step 3: Confirm migration on a real `claude-chat.db` snapshot**

```bash
# Find the live DB path
grep CHAT_DB_PATH .env

# Copy it (don't touch the live one)
cp <path-from-env> /tmp/claude-chat-snapshot.db

# Run only the ChatDB constructor (which runs MIGRATIONS)
.venv/bin/python -c "from src.chat_db import ChatDB; ChatDB('/tmp/claude-chat-snapshot.db')"

# Verify both columns exist + existing rows are NULL (behavior-preserving)
sqlite3 /tmp/claude-chat-snapshot.db "PRAGMA table_info(tasks)" | grep mutates_repo
sqlite3 /tmp/claude-chat-snapshot.db "PRAGMA table_info(outbound_emails)" | grep task_id
sqlite3 /tmp/claude-chat-snapshot.db "SELECT COUNT(*) FROM tasks"
sqlite3 /tmp/claude-chat-snapshot.db "SELECT COUNT(*) FROM tasks WHERE mutates_repo IS NULL"
```

Expected: both columns present; total task count == NULL-mutates_repo count (no behavior change for existing rows).

**Do NOT restart the live `claude-chat` service to "test" the migration.** That severs every active MCP session and breaks the dashboard. The migration runs on next service restart automatically — no manual action needed.

### Step 4: Self-review checklist

Walk through the README's "Self-review checklist" section. Every item must pass:

- [ ] Spec coverage (reviewer's blockers 1–5 + smaller issues a–e)
- [ ] Placeholder scan empty
- [ ] Type consistency
- [ ] Name consistency
- [ ] No stale imports
- [ ] Strict 200-line
- [ ] 100% coverage

### Step 5: Ready-for-PR

- [ ] **Run final test suite + capture exact count**

```
.venv/bin/pytest tests/ -q
```

- [ ] **Skim `git log master..HEAD`** — confirm the commit story reads cleanly (see README's "When done" section).

- [ ] **Hand back to the user**

Report:
- Final test count
- List of new/modified files
- Proposed PR title: `fix: skip dirty-repo gate for read-only tasks; reuse prior branch on email follow-ups`

Ask whether to push and open the PR with `gh pr create`.

**Do NOT push or open the PR autonomously.** Per memory `feedback_one_question_per_prompt` and CLAUDE.md's "actions visible to others" rule, both push and PR creation are user-confirmed actions.


================================================================================
END FILE: phase-h-finalize.md
================================================================================
