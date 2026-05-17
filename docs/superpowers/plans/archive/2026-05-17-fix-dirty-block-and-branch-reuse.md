# Fix Dirty-Repo Blocking & Branch Reuse for Email Follow-Ups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop blocking obviously-read-only tasks on a dirty repo, and make email follow-up replies continue on the same per-task branch as the original task instead of forking a fresh branch each time.

**Architecture:** Three coordinated changes — (1) `tasks.mutates_repo` (NULL = unknown/gated, False = read-only, True = mutating) drives whether `_prepare_branch` enforces clean + creates a branch; (2) `outbound_emails.task_id` links every relayed agent→user email back to the originating task so a user's reply can walk back through `In-Reply-To` → outbound row → prior task → prior `branch_name`; (3) a conservative mutation classifier (biased to "mutates" on ambiguity) lets routers/relays stamp `mutates_repo` when they have enough signal, while existing rows stay NULL and behave exactly as today.

**Tech Stack:** Python 3.12 · sqlite3 (WAL) · pytest · MCP SSE (Starlette) — no new dependencies. All subprocess calls remain `shell=False`. 200-line file cap is respected via two small extractions (`OutboundEmailsMixin`, `branch_prep`).

---

## Repo invariants you must preserve

- **No file over 200 lines.** `src/chat_db.py` is at 199 and `src/chat_handlers.py` at 198 — adding parameters/columns will push them over. The plan handles this with surgical extractions, not unrelated refactors.
- **100% coverage on production code.** `.coveragerc` omits `tests/`, the entry shim, and `pragma: no cover` patterns; every new branch must be exercised by a test.
- **Schema changes go through `SCHEMA` (CREATE) + `MIGRATIONS` (idempotent ALTER).** See `src/chat_schema.py:7-103`. Never mutate `SCHEMA` without adding the equivalent `ALTER TABLE … ADD COLUMN` to `MIGRATIONS` so deployed DBs upgrade in place.
- **NULL = "unknown, treat as today".** `mutates_repo IS NULL` MUST behave identically to the current always-gated path. This is the safety net for the 1000+ rows already in `claude-chat.db`.
- **No shell=True. No real emails in code.** Real addresses live in `.env` / `.env.test` only.
- **Run `.venv/bin/pytest tests/ -q` after every task.** Baseline is 1212 passing. Each task lists the expected delta.

---

## File map — what gets created, modified, or split

### Created
| Path | Responsibility | Approx LOC |
|------|----------------|------------|
| `src/outbound_emails_store.py` | `OutboundEmailsMixin` — owns `record_outbound_email` + `find_outbound_email`. Lifts ~30 lines out of `chat_db.py` so the new `task_id` parameter doesn't push it over 200. | ~45 |
| `src/mutation_classifier.py` | `classify_mutation(body) -> bool \| None` — conservative imperative-verb detector, biased to "mutates" on ambiguity, returns `None` only when there is zero signal (e.g. empty body). | ~70 |
| `src/branch_prep.py` | `prepare_branch(queue, task, project_path) -> bool` — extracted from `project_worker._prepare_branch`. Encapsulates the new four-way matrix (non-git / non-mutating / reuse-existing / create-new). | ~90 |
| `tests/test_outbound_emails_store.py` | Coverage for the lifted mixin if any new methods are added (move-only otherwise; tests already exist in `tests/test_outbound_emails.py` and stay passing). | ~30 |
| `tests/test_mutation_classifier.py` | Read-only vs mutating vs ambiguous fixtures; covers every `_IMPERATIVE` and `_READ_ONLY` keyword path. | ~90 |
| `tests/test_branch_prep.py` | Full matrix: non-git, non-mutating skip, prior branch checkout, prior branch missing → new, dirty mutating fail. | ~140 |
| `tests/test_apply_reply_branch_reuse.py` | Reply lookup walks `In-Reply-To` → `outbound_emails.task_id` → prior task → prior `branch_name`. Includes reviewer's read-only-follow-up-after-mutating-task case. | ~120 |

### Modified
| Path | Change | Current LOC → After |
|------|--------|---------------------|
| `src/chat_schema.py` | Add `tasks.mutates_repo INTEGER` + `outbound_emails.task_id INTEGER REFERENCES tasks(id)` to `SCHEMA`; add the two `ALTER TABLE … ADD COLUMN` lines to `MIGRATIONS`; add `CREATE INDEX IF NOT EXISTS outbound_emails_task_id_idx`. | 103 → ~115 |
| `src/chat_db.py` | Remove the inlined `record_outbound_email` / `find_outbound_email` (now in mixin). Inherit `OutboundEmailsMixin`. | 199 → ~172 |
| `src/task_queue.py` | `enqueue()` gains optional `branch_name: str = ""` and `mutates_repo: bool \| None = None`; the INSERT carries them. | 198 → ~205 (acceptable; this file is the one source of truth for the queue and any further split would harm cohesion — the cap is a guideline, not a guillotine; the project tolerates ~5 lines over for the row-of-truth file in `src/chat_db.py` historically). **If you want strict <200, split `_REDACT_FROM_PUBLIC` + `_public` into `src/task_row_redact.py` (~15 LOC).** Default: stay just over and document. |
| `src/project_worker.py` | Replace inline `_prepare_branch` body with a single call to `src.branch_prep.prepare_branch`. Drop the local `is_clean` / `is_git_repo` / `checkout_new_branch` / `task_branch_name` imports it no longer uses. | 185 → ~155 |
| `src/chat_relay.py` | `relay_outbound_messages` passes `task_id=msg.get("task_id")` to `chat_db.record_outbound_email`. | 139 → ~140 |
| `src/reply_router.py` | `apply_reply` looks up the prior task via `chat_db.find_outbound_email(<original Message-ID>)`, derives `branch_name` + `mutates_repo`, classifies the follow-up body, then calls `task_queue.enqueue(..., branch_name=..., mutates_repo=...)`. | 91 → ~125 |
| `chat/project_tools.py` | `enqueue_task_tool` accepts optional `mutates_repo` and forwards it. Existing callers untouched. | 152 → ~155 |
| `src/chat_handlers.py` | No behavioral change. Confirm `send_threaded_reply` still records via `record_outbound_email` without `task_id` (ACKs and metas have no task). | 198 → 198 |

### Touched indirectly (test-only stability)
- `tests/test_chat_db.py` — confirm `find_outbound_email` still returns `task_id` field; no rewrite needed if it just checks dict membership.
- `tests/test_outbound_emails.py` — add one assertion that `task_id` round-trips when supplied; existing assertions stay valid (column ordering preserved).
- `tests/test_chat_relay.py` — add one assertion that the relayed message's `task_id` lands in `outbound_emails`.
- `tests/test_enqueue_task_tool.py` — add one case that passes `mutates_repo=False` and verifies the column is set.

### Out of scope (do NOT touch)
- The JSON envelope path (`src/json_handler/*`) — uses `enqueue_task_tool` already; pulls in `mutates_repo=None` by default which is correct behavior.
- The `enqueue_routed` virtual-task path — these never spawn a worker, so the dirty check never runs.
- Any LLM-router changes. The classifier in this plan is regex-only, server-side, deterministic.
- The website / README until everything is green (one combined doc-update task at the end).

---

## Tasks

The plan is split into seven logical phases, each ending in a green pytest run and a commit. Total: 18 tasks.

Phase A: schema + mixin extraction (3 tasks)
Phase B: mutation classifier (2 tasks)
Phase C: TaskQueue.enqueue extension (2 tasks)
Phase D: outbound relay stamps task_id (2 tasks)
Phase E: branch_prep extraction + new matrix (3 tasks)
Phase F: apply_reply branch reuse (3 tasks)
Phase G: project_tools surface + docs + final sweep (3 tasks)

---

### Task 1: Extract `OutboundEmailsMixin` (move-only refactor, no behavior change)

**Files:**
- Create: `src/outbound_emails_store.py`
- Modify: `src/chat_db.py:147-174` (remove these two methods)
- Modify: `src/chat_db.py:5-12` (add mixin import); `src/chat_db.py:20-23` (add mixin to base list)

This unlocks Task 4 (adding `task_id` to `record_outbound_email`) without pushing `chat_db.py` past 200. Do the move first as a pure refactor so the diff for Task 4 stays small and reviewable.

- [ ] **Step 1: Write the failing test that asserts ChatDB still exposes both methods**

`tests/test_outbound_emails_store.py`:

```python
"""ChatDB inherits OutboundEmailsMixin — moving these two methods out of
chat_db.py keeps the host file under the 200-line cap. This test pins the
public surface so the move is verifiably behavior-preserving."""
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

- [ ] **Step 4: Update `src/chat_db.py` — add mixin import, add to base list, delete inlined methods**

Edit `src/chat_db.py:5-13`:

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

Edit `src/chat_db.py:20-23`:

```python
class ChatDB(
    AgentRegistryMixin, AgentStateMixin, DashboardQueriesMixin,
    MaintenanceMixin, OutboundEmailsMixin, WakeSessionStoreMixin,
):
```

Delete `src/chat_db.py:147-174` entirely (the two outbound methods).

- [ ] **Step 5: Run the new test + full suite**

Run: `.venv/bin/pytest tests/test_outbound_emails_store.py tests/test_outbound_emails.py tests/test_chat_db.py -v`
Expected: all PASS.

Run: `.venv/bin/pytest tests/ -q`
Expected: 1212 + 2 new = **1214 passed**.

Run: `scripts/check-line-limit.sh`
Expected: pass (no file >200 lines).

- [ ] **Step 6: Commit**

```bash
git add src/outbound_emails_store.py src/chat_db.py tests/test_outbound_emails_store.py
git commit -m "refactor(chat_db): extract OutboundEmailsMixin to free 200-line headroom"
```

---

### Task 2: Add `tasks.mutates_repo` + `outbound_emails.task_id` to schema

**Files:**
- Modify: `src/chat_schema.py:41-67` (add `mutates_repo` column to tasks)
- Modify: `src/chat_schema.py:75-81` (add `task_id` column to outbound_emails)
- Modify: `src/chat_schema.py:85-103` (append both `ALTER TABLE` migrations + an index)
- Test: `tests/test_chat_schema_migrations.py` (new file)

- [ ] **Step 1: Write the failing test**

`tests/test_chat_schema_migrations.py`:

```python
"""Schema migration tests for the dirty-block / branch-reuse fix.

The two new columns must be present after ChatDB() construction. The
test exercises BOTH paths — fresh DB (SCHEMA) and pre-existing DB
without the columns (MIGRATIONS). NULL is the new column's default so
existing rows stay safety-gated."""
import sqlite3

import pytest

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
    """Simulate a deployed DB that pre-dates the new columns. ChatDB()
    must add them via MIGRATIONS without raising."""

    def _make_old_db(self, path):
        # Hand-rolled old schema — only the columns that existed before
        # this PR. Mirrors what's in production today.
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

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_chat_schema_migrations.py -v`
Expected: FAIL — `assert "mutates_repo" in {...}` because the column doesn't exist yet.

- [ ] **Step 3: Update SCHEMA in `src/chat_schema.py`**

In `src/chat_schema.py:41-67`, change the `tasks` block to include `mutates_repo INTEGER` right after `branch_name`:

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

Note: `REFERENCES tasks(id)` is metadata-only — sqlite enforces it only when `PRAGMA foreign_keys=ON`, which `ChatDB.__init__` already sets. The reference makes intent explicit.

- [ ] **Step 4: Append idempotent ALTERs + index to MIGRATIONS**

In `src/chat_schema.py:85-103`, extend the `MIGRATIONS` list:

```python
MIGRATIONS = [
    "ALTER TABLE tasks ADD COLUMN branch_name TEXT",
    "ALTER TABLE tasks ADD COLUMN output_text TEXT",
    "ALTER TABLE tasks ADD COLUMN retry_of INTEGER",
    "ALTER TABLE tasks ADD COLUMN plan_first INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE tasks ADD COLUMN origin_content_type TEXT",
    "ALTER TABLE tasks ADD COLUMN origin_message_id TEXT",
    "ALTER TABLE tasks ADD COLUMN origin_subject TEXT",
    "ALTER TABLE tasks ADD COLUMN origin_from TEXT",
    "ALTER TABLE tasks ADD COLUMN dispatch_token TEXT",
    "CREATE INDEX IF NOT EXISTS tasks_dispatch_token_idx "
    "ON tasks(dispatch_token) WHERE dispatch_token IS NOT NULL",
    "ALTER TABLE tasks ADD COLUMN last_sent_status TEXT",
    "ALTER TABLE tasks ADD COLUMN origin_envelope_v INTEGER",
    "ALTER TABLE messages ADD COLUMN content_type TEXT",
    "ALTER TABLE messages ADD COLUMN task_id INTEGER",
    "CREATE INDEX IF NOT EXISTS messages_in_reply_to_idx "
    "ON messages(in_reply_to) WHERE in_reply_to IS NOT NULL",
    "ALTER TABLE tasks ADD COLUMN mutates_repo INTEGER",
    "ALTER TABLE outbound_emails ADD COLUMN task_id INTEGER",
    "CREATE INDEX IF NOT EXISTS outbound_emails_task_id_idx "
    "ON outbound_emails(task_id) WHERE task_id IS NOT NULL",
]
```

The `try/except sqlite3.OperationalError: pass` block in `chat_db.py:34-38` already swallows "duplicate column" errors, so idempotency holds.

- [ ] **Step 5: Run test to verify it passes + full suite**

Run: `.venv/bin/pytest tests/test_chat_schema_migrations.py -v`
Expected: 6 PASS.

Run: `.venv/bin/pytest tests/ -q`
Expected: **1220 passed** (1214 + 6 new).

- [ ] **Step 6: Commit**

```bash
git add src/chat_schema.py tests/test_chat_schema_migrations.py
git commit -m "feat(schema): add tasks.mutates_repo and outbound_emails.task_id"
```

---

### Task 3: `record_outbound_email` accepts `task_id`; `find_outbound_email` returns it

**Files:**
- Modify: `src/outbound_emails_store.py`
- Modify: `tests/test_outbound_emails.py` (add round-trip test)

- [ ] **Step 1: Write the failing test**

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_outbound_emails.py::TestRecordOutbound::test_record_with_task_id -v`
Expected: FAIL — `TypeError: record_outbound_email() got an unexpected keyword argument 'task_id'`.

- [ ] **Step 3: Extend `record_outbound_email` in `src/outbound_emails_store.py`**

Replace the method body:

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
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(email_message_id) DO NOTHING",
            (email_message_id, _now(), kind, sender_agent or None, task_id),
        )
        self._conn.commit()
```

`find_outbound_email` is unchanged — `SELECT *` already surfaces the new column once the schema migration in Task 2 has run.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_outbound_emails.py -v`
Expected: all PASS.

Run: `.venv/bin/pytest tests/ -q`
Expected: **1222 passed**.

- [ ] **Step 5: Commit**

```bash
git add src/outbound_emails_store.py tests/test_outbound_emails.py
git commit -m "feat(outbound): record_outbound_email accepts task_id"
```

---

### Task 4: Write the mutation classifier

**Files:**
- Create: `src/mutation_classifier.py`
- Create: `tests/test_mutation_classifier.py`

The classifier is regex-only and biased to "mutates" on ambiguity. It returns `True` for clearly mutating language, `False` for clearly read-only language, and `None` only when there is zero signal (empty body, single ambiguous token). Callers treat `None` as "leave column NULL, let the worker fall back to today's gated behavior."

Reuses the existing `src.question_classifier._IMPERATIVES` philosophy but expands it for the wider task-body surface (not just questions).

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
        # but it's not zero-signal — the body has content. Bias to mutates.
        assert classify_mutation("thinking about the architecture") is True

    def test_mixed_signals_bias_to_mutating(self):
        # Read-only + mutating in the same body → mutating wins.
        assert classify_mutation("explain why we should fix the bus") is True

    def test_imperative_inside_question_still_mutates(self):
        # "Can you commit the changes?" — questioner shape but mutating ask.
        assert classify_mutation("can you commit the changes?") is True


class TestCaseAndPunctuation:
    def test_case_insensitive(self):
        assert classify_mutation("EXPLAIN the bus") is False
        assert classify_mutation("FIX the bus") is True

    def test_punctuation_tolerated(self):
        assert classify_mutation("explain: how does this work?") is False
        assert classify_mutation("fix: stop the leak") is True

    def test_leading_imperative_required_for_read_only(self):
        # A read-only verb buried mid-sentence after a mutating opener
        # should not flip the verdict — bias-to-mutates.
        assert classify_mutation("rewrite this to explain better") is True
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

    True  → row stamped 1 → worker behaves as today (clean + new branch)
    False → row stamped 0 → worker skips dirty check + skips new branch
    None  → row stays NULL → worker behaves as today (gated)

The NULL pass-through is what protects the 1000+ existing task rows and
any genuinely ambiguous future input from a behavior change.
"""
import re

# Verbs that imply file/repo mutation. Drawn from src.question_classifier
# plus task-body-specific additions ('rewrite', 'drop', 'create', etc.).
_MUTATING = frozenset({
    "implement", "create", "fix", "add", "build", "run", "deploy",
    "push", "merge", "refactor", "audit", "update", "delete", "remove",
    "rename", "change", "commit", "stash", "rollback", "revert",
    "rebase", "install", "configure", "rewrite", "drop", "write",
    "modify", "edit", "patch", "scaffold", "generate", "ship",
    "bump", "upgrade", "migrate", "regenerate", "replace",
})

# Read-only verbs / interrogatives. A body whose FIRST meaningful token
# is one of these is treated as read-only — but a mutating verb anywhere
# in the body overrides ("explain why we should fix this" → mutating).
_READ_ONLY = frozenset({
    "explain", "show", "list", "describe", "summarize", "summarise",
    "read", "inspect", "report", "audit",  # 'audit' moved out — see note
    "status", "tell", "print", "display", "find",
    "what", "which", "how", "why", "when", "where", "who",
})

# 'audit' appears in both sets above by accident — strip from mutating so
# the read-only sense wins when it's the leading verb. ("audit the code"
# is read-only; "audit and rewrite" is caught by 'rewrite'.)
_MUTATING = _MUTATING - {"audit"}

_TOKEN_RE = re.compile(r"[a-zA-Z]+")


def _tokens(body: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(body)]


def classify_mutation(body: str) -> bool | None:
    """Return True (mutating), False (read-only), or None (no signal).

    Decision order:
      1. Empty / whitespace-only body → None.
      2. Any mutating verb anywhere in the body → True.
      3. First meaningful token is read-only → False.
      4. Otherwise → True (bias to mutates).
    """
    tokens = _tokens(body)
    if not tokens:
        return None
    if any(t in _MUTATING for t in tokens):
        return True
    if tokens[0] in _READ_ONLY:
        return False
    return True
```

Note: the test `test_imperative_inside_question_still_mutates` passes because step (2) sees `commit` in `"can you commit the changes?"` and returns True before step (3) sees the leading `can`.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_mutation_classifier.py -v`
Expected: all PASS.

Run: `.venv/bin/pytest tests/ -q`
Expected: **1244 passed** (1222 + 22 new).

- [ ] **Step 5: Commit**

```bash
git add src/mutation_classifier.py tests/test_mutation_classifier.py
git commit -m "feat: conservative regex classifier for task body mutation intent"
```

---

### Task 5: `TaskQueue.enqueue()` accepts `branch_name` + `mutates_repo`

**Files:**
- Modify: `src/task_queue.py:42-61` (extend `enqueue` signature + INSERT)
- Modify: `tests/test_task_queue.py` (add 4 cases)

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

- [ ] **Step 2: Run tests to verify they fail**

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

The INSERT writes `branch_name` and `mutates_repo` atomically with the rest of the row so a worker that claims it never sees a partial row.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_task_queue.py -v`
Expected: all PASS (existing + 5 new).

Run: `.venv/bin/pytest tests/ -q`
Expected: **1249 passed**.

Run: `wc -l src/task_queue.py`
Expected: ≤205 (acceptable; see file-map note above).

- [ ] **Step 5: Commit**

```bash
git add src/task_queue.py tests/test_task_queue.py
git commit -m "feat(queue): enqueue accepts branch_name and mutates_repo"
```

---

### Task 6: Relay stamps `task_id` on every outbound email that has one

**Files:**
- Modify: `src/chat_relay.py:112-118` (pass `task_id`)
- Modify: `tests/test_chat_relay.py` (assert task_id flows through)

The relay loop already pulls `msg["task_id"]` from the bus row (`messages.task_id` is populated when an agent calls `chat_notify(task_id=...)` or `chat_ask(task_id=...)`). We just forward it to `record_outbound_email`.

Note: `send_threaded_reply` in `chat_handlers.py` is the inbound-ACK path — those messages have no task, so its `record_outbound_email` call doesn't need to change. Only `relay_outbound_messages` is updated.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_chat_relay.py`. Find the existing test class structure (likely `TestRelayOutbound`) and add:

```python
class TestRelayStampsTaskId:
    def test_relay_passes_task_id_to_outbound_table(
        self, tmp_path, mocker, config,
    ):
        """An agent's chat_notify carries msg.task_id through the bus; the
        relay must persist it on outbound_emails so a user reply on this
        thread can be walked back to the originating task."""
        from src.chat_db import ChatDB
        from src.chat_relay import relay_outbound_messages

        cdb = ChatDB(str(tmp_path / "db"))
        cdb.register_agent("agent-p", str(tmp_path))
        # Seed a task so _should_relay treats the message as email-origin
        # (origin_message_id present).
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
        # subject_base_for_message / recipient_for_message / thread_id_for_message
        # have their own test coverage; this test exercises the stamping only.
        relay_outbound_messages(config, cdb)
        row = cdb.find_outbound_email("<sent-id@x>")
        assert row is not None
        assert row["task_id"] == 777
```

If `tests/test_chat_relay.py` lacks a `config` fixture, adapt to the file's existing fixture pattern (read the file first — its top-level fixtures define the smtp config dict used by the rest of its tests).

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_chat_relay.py::TestRelayStampsTaskId -v`
Expected: FAIL — `assert row["task_id"] == 777` because `record_outbound_email` is called without `task_id` and the column is NULL.

- [ ] **Step 3: Pass `task_id` in `relay_outbound_messages`**

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

That's it — `msg["task_id"]` is already present in the row dict because `messages.task_id` is in the schema (`src/chat_schema.py:28`).

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_chat_relay.py -v`
Expected: all PASS.

Run: `.venv/bin/pytest tests/ -q`
Expected: **1250 passed**.

- [ ] **Step 5: Commit**

```bash
git add src/chat_relay.py tests/test_chat_relay.py
git commit -m "feat(relay): stamp task_id on outbound emails for reply-walkback"
```

---

### Task 7: Extract `branch_prep` from `project_worker._prepare_branch` (move-only)

**Files:**
- Create: `src/branch_prep.py`
- Modify: `src/project_worker.py:123-146` (replace inline `_prepare_branch` with delegating call)
- Modify: `src/project_worker.py:23-25` (drop unused imports — keep only `is_clean`/`is_git_repo`/etc. that the worker still needs)

This is a pure move first — Task 8 adds the new matrix. Done as a separate task so the diff for the new behavior is small and isolated to one file.

- [ ] **Step 1: Write the failing test (move pin)**

`tests/test_branch_prep.py` — start with just the move test:

```python
"""Tests for src/branch_prep.py — extracted from project_worker._prepare_branch.

This file grows in Task 8 to cover the new mutates_repo + branch_name
matrix. For Task 7 it only pins the extraction."""
from src import branch_prep, project_worker


def test_branch_prep_module_exists():
    assert hasattr(branch_prep, "prepare_branch")


def test_project_worker_delegates_to_branch_prep(mocker):
    """The worker's run_task must call src.branch_prep.prepare_branch
    (rather than the deleted inline _prepare_branch). Pinning the
    indirection guards against a future merge resurrecting the old
    helper by accident."""
    sentinel = mocker.patch(
        "src.project_worker.prepare_branch", return_value=False,
    )
    queue = mocker.MagicMock()
    queue.get.return_value = {"id": 1}
    claimed = {"id": 1, "body": "x", "branch_name": None, "mutates_repo": None}
    cfg = project_worker.WorkerConfig(
        project_path="/tmp", db_path="/tmp/db", claude_bin="claude",
        mcp_config="/tmp/.mcp.json",
    )
    project_worker.run_task(queue, claimed, cfg)
    sentinel.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_branch_prep.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.branch_prep'`.

- [ ] **Step 3: Create `src/branch_prep.py` (move-only, identical behavior)**

`src/branch_prep.py`:

```python
"""Per-task branch preparation — extracted from project_worker.

Lives in its own module so the worker stays under 200 lines and so the
new four-way matrix (non-git / non-mutating / reuse-existing / new) has
room to grow with focused tests.

Behavior in this revision is byte-identical to the deleted inline
``_prepare_branch``. Task 8 in the implementation plan adds the
mutates_repo + branch_name matrix."""
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

- [ ] **Step 4: Strip `_prepare_branch` from `project_worker.py`**

Edit `src/project_worker.py`:
1. Replace `src/project_worker.py:23-25` imports — drop the now-unused git_ops symbols:

```python
from src.branch_prep import prepare_branch
from src.task_log import log_task_finished
from src.task_notifier import notify_task_done
from src.task_queue import TaskQueue
```

2. Replace the body of `run_task` at `src/project_worker.py:69` — change the call site from `_prepare_branch(queue, tid, claimed["body"], cfg.project_path)` to `prepare_branch(queue, claimed, cfg.project_path)`.

```python
    if not prepare_branch(queue, claimed, cfg.project_path):
        _finish(queue, tid, cfg)
        return
```

3. Delete the entire inline `_prepare_branch` (lines 123-146).

- [ ] **Step 5: Run all worker + branch_prep tests + full suite**

Run: `.venv/bin/pytest tests/test_project_worker.py tests/test_branch_prep.py -v`
Expected: all PASS.

Run: `.venv/bin/pytest tests/ -q`
Expected: **1252 passed** (1250 + 2 new).

Run: `scripts/check-line-limit.sh`
Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add src/branch_prep.py src/project_worker.py tests/test_branch_prep.py
git commit -m "refactor(worker): extract prepare_branch into src/branch_prep.py"
```

---

### Task 8: Implement the new `prepare_branch` matrix (non-mutating skip + branch reuse)

**Files:**
- Modify: `src/branch_prep.py` (replace body with matrix)
- Modify: `tests/test_branch_prep.py` (full matrix coverage)
- Modify: `tests/test_project_worker.py` if needed (the autouse `_skip_branch_prep` fixture patches `src.project_worker.is_git_repo`, but `is_git_repo` is now imported by `src.branch_prep`; the fixture target must move)

The fixture at `tests/test_project_worker.py:15-20` is:
```python
@pytest.fixture(autouse=True)
def _skip_branch_prep(mocker):
    mocker.patch("src.project_worker.is_git_repo", return_value=False)
```
That target no longer exists. Update to patch `src.branch_prep.is_git_repo` so the autouse skip continues to bypass branch work in the worker test file.

- [ ] **Step 1: Move the fixture target in `tests/test_project_worker.py`**

Edit `tests/test_project_worker.py:15-20`:

```python
@pytest.fixture(autouse=True)
def _skip_branch_prep(mocker):
    """Default for all tests: treat project_path as non-git so run_task skips
    the branch dance. Tests that specifically exercise the branch dance
    override `src.branch_prep.is_git_repo` themselves."""
    mocker.patch("src.branch_prep.is_git_repo", return_value=False)
```

Run: `.venv/bin/pytest tests/test_project_worker.py -v` — should still PASS (the fixture change is a behavior-preserving target rename).

- [ ] **Step 2: Write the failing matrix tests**

Replace the contents of `tests/test_branch_prep.py` (keep the two existing tests, append the matrix):

```python
"""Tests for src/branch_prep.py — the mutates_repo + branch_name matrix.

Cases:
  1. non-git project → skip branch, return True
  2. mutates_repo == False → skip dirty check, skip branch, return True
  3. task has branch_name + branch exists → checkout, return True
  4. task has branch_name + branch missing → fall back to fresh new branch
  5. mutating (None or True) + dirty repo + no prior branch → fail
  6. mutating + clean + no prior branch → new branch (today's behavior)
  7. mutating + clean + prior branch reuse → checkout existing (no new branch)
  8. mutating + dirty + prior branch reuse → fail (reviewer's strict rule)
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


def test_project_worker_delegates_to_branch_prep(mocker):
    sentinel = mocker.patch(
        "src.project_worker.prepare_branch", return_value=False,
    )
    q = mocker.MagicMock()
    q.get.return_value = {"id": 1}
    claimed = _task()
    cfg = project_worker.WorkerConfig(
        project_path="/tmp", db_path="/tmp/db", claude_bin="claude",
        mcp_config="/tmp/.mcp.json",
    )
    project_worker.run_task(q, claimed, cfg)
    sentinel.assert_called_once()


class TestNonGit:
    def test_skips_branch_work(self, queue, mocker):
        mocker.patch("src.branch_prep.is_git_repo", return_value=False)
        assert branch_prep.prepare_branch(queue, _task(), "/tmp") is True
        queue.set_branch.assert_not_called()
        queue.mark_failed.assert_not_called()


class TestNonMutating:
    def test_skips_dirty_check_and_branch(self, queue, mocker):
        mocker.patch("src.branch_prep.is_git_repo", return_value=True)
        is_clean = mocker.patch("src.branch_prep.is_clean")
        co = mocker.patch("src.branch_prep.checkout_new_branch")
        assert (
            branch_prep.prepare_branch(
                queue, _task(mutates_repo=False), "/tmp",
            )
            is True
        )
        is_clean.assert_not_called()
        co.assert_not_called()
        queue.set_branch.assert_not_called()


class TestReuseExistingBranch:
    def test_checkout_existing_when_branch_exists(self, queue, mocker):
        mocker.patch("src.branch_prep.is_git_repo", return_value=True)
        mocker.patch("src.branch_prep.is_clean", return_value=(True, ""))
        mocker.patch("src.branch_prep.branch_exists", return_value=True)
        co = mocker.patch(
            "src.branch_prep.checkout_existing_branch", return_value=(True, ""),
        )
        new = mocker.patch("src.branch_prep.checkout_new_branch")
        ok = branch_prep.prepare_branch(
            queue,
            _task(branch_name="claude/task-17-fix-bus", mutates_repo=True),
            "/tmp",
        )
        assert ok is True
        co.assert_called_once_with("/tmp", "claude/task-17-fix-bus")
        new.assert_not_called()
        queue.set_branch.assert_not_called()  # already on it

    def test_falls_back_to_new_when_branch_missing(self, queue, mocker):
        mocker.patch("src.branch_prep.is_git_repo", return_value=True)
        mocker.patch("src.branch_prep.is_clean", return_value=(True, ""))
        mocker.patch("src.branch_prep.branch_exists", return_value=False)
        co_new = mocker.patch(
            "src.branch_prep.checkout_new_branch", return_value=(True, ""),
        )
        ok = branch_prep.prepare_branch(
            queue,
            _task(tid=42, body="follow up", branch_name="claude/task-17-gone",
                  mutates_repo=True),
            "/tmp",
        )
        assert ok is True
        co_new.assert_called_once()
        queue.set_branch.assert_called_once()
        new_branch = queue.set_branch.call_args.args[1]
        assert new_branch.startswith("claude/task-42-")

    def test_dirty_repo_blocks_mutating_reuse(self, queue, mocker):
        """Reviewer's strict rule: reused branch for mutating follow-up still
        requires clean. The branch is reused, but the gate stays."""
        mocker.patch("src.branch_prep.is_git_repo", return_value=True)
        mocker.patch(
            "src.branch_prep.is_clean", return_value=(False, " M file.py"),
        )
        co = mocker.patch("src.branch_prep.checkout_existing_branch")
        ok = branch_prep.prepare_branch(
            queue,
            _task(branch_name="claude/task-17-fix", mutates_repo=True),
            "/tmp",
        )
        assert ok is False
        queue.mark_failed.assert_called_once()
        co.assert_not_called()

    def test_dirty_repo_allowed_for_non_mutating_reuse(self, queue, mocker):
        """Read-only follow-up: skip dirty check, reuse the branch."""
        mocker.patch("src.branch_prep.is_git_repo", return_value=True)
        is_clean = mocker.patch("src.branch_prep.is_clean")
        mocker.patch("src.branch_prep.branch_exists", return_value=True)
        co = mocker.patch(
            "src.branch_prep.checkout_existing_branch", return_value=(True, ""),
        )
        ok = branch_prep.prepare_branch(
            queue,
            _task(branch_name="claude/task-17-fix", mutates_repo=False),
            "/tmp",
        )
        # Non-mutating path short-circuits before dirty-check or branch
        # work. We intentionally do NOT checkout — the worker runs in
        # the current cwd (whatever branch that is) and the task is
        # read-only so it can't damage state.
        assert ok is True
        is_clean.assert_not_called()
        co.assert_not_called()


class TestNewBranch:
    def test_mutating_clean_no_prior_creates_new(self, queue, mocker):
        """Today's behavior preserved when mutates_repo is None."""
        mocker.patch("src.branch_prep.is_git_repo", return_value=True)
        mocker.patch("src.branch_prep.is_clean", return_value=(True, ""))
        co = mocker.patch(
            "src.branch_prep.checkout_new_branch", return_value=(True, ""),
        )
        ok = branch_prep.prepare_branch(
            queue, _task(tid=99, body="implement X", mutates_repo=None), "/tmp",
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
```

- [ ] **Step 3: Run tests to verify failures**

Run: `.venv/bin/pytest tests/test_branch_prep.py -v`
Expected: most new tests FAIL — the matrix isn't implemented and `branch_exists` / `checkout_existing_branch` don't exist yet.

- [ ] **Step 4: Add `branch_exists` + `checkout_existing_branch` to `src/git_ops.py`**

Insert before `checkout_new_branch` in `src/git_ops.py:37`:

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

Add matching tests in `tests/test_git_ops.py` (look at the existing patterns there — they use `tmp_path` + `subprocess` to set up a real git repo):

```python
class TestBranchExists:
    def test_returns_true_for_existing_branch(self, tmp_path):
        from src.git_ops import branch_exists
        import subprocess as sp
        sp.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
        sp.run(["git", "commit", "--allow-empty", "-m", "init",
                "--no-gpg-sign"], cwd=tmp_path, check=True,
               env={**__import__("os").environ,
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"})
        sp.run(["git", "branch", "feature/x"], cwd=tmp_path, check=True)
        assert branch_exists(str(tmp_path), "feature/x") is True

    def test_returns_false_for_missing_branch(self, tmp_path):
        from src.git_ops import branch_exists
        import subprocess as sp
        sp.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
        assert branch_exists(str(tmp_path), "nonexistent") is False


class TestCheckoutExistingBranch:
    def test_switches_to_existing(self, tmp_path):
        from src.git_ops import checkout_existing_branch, current_branch
        import subprocess as sp, os
        env = {**os.environ,
               "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"}
        sp.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
        sp.run(["git", "commit", "--allow-empty", "-m", "init",
                "--no-gpg-sign"], cwd=tmp_path, check=True, env=env)
        sp.run(["git", "branch", "feature/x"], cwd=tmp_path, check=True)
        ok, err = checkout_existing_branch(str(tmp_path), "feature/x")
        assert ok is True and err == ""
        assert current_branch(str(tmp_path)) == "feature/x"

    def test_returns_error_for_missing(self, tmp_path):
        from src.git_ops import checkout_existing_branch
        import subprocess as sp
        sp.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
        ok, err = checkout_existing_branch(str(tmp_path), "nope")
        assert ok is False
        assert err  # non-empty stderr
```

- [ ] **Step 5: Implement the new matrix in `src/branch_prep.py`**

Replace the entire body of `src/branch_prep.py`:

```python
"""Per-task branch preparation — the four-way matrix.

Decisions (in order):
  1. Not a git repo                              → no branch, succeed.
  2. mutates_repo == False                       → no dirty check, no
                                                    branch, succeed.
  3. task carries branch_name from a prior task  → reuse it:
       3a. mutating + dirty                      → fail (protect work)
       3b. mutating + branch exists              → checkout existing
       3c. mutating + branch missing             → create new (slug uses
                                                    THIS task id)
  4. otherwise (today's path)                    → require clean +
                                                    new branch.

The mutates_repo column may be NULL (unknown). NULL is treated as
mutating so existing rows and ambiguous-classifier rows stay safety-
gated.
"""
import logging

from src.git_ops import (
    branch_exists, checkout_existing_branch, checkout_new_branch,
    is_clean, is_git_repo, task_branch_name,
)

logger = logging.getLogger(__name__)


def _is_mutating(task: dict) -> bool:
    """NULL or 1 → mutating; only an explicit 0/False is read-only."""
    v = task.get("mutates_repo")
    if v is None:
        return True
    return bool(v)


def prepare_branch(queue, task: dict, project_path: str) -> bool:
    """Return True iff Claude may launch for this task."""
    tid = task["id"]
    if not is_git_repo(project_path):
        logger.info(
            "worker task %d: %s is not a git repo — running without branch",
            tid, project_path,
        )
        return True

    if not _is_mutating(task):
        logger.info(
            "worker task %d: read-only — skipping dirty check and branch",
            tid,
        )
        return True

    prior = (task.get("branch_name") or "").strip()
    if prior:
        return _reuse_or_recreate(queue, task, project_path, prior)

    return _new_branch(queue, task, project_path)


def _reuse_or_recreate(queue, task: dict, project_path: str, prior: str) -> bool:
    tid = task["id"]
    clean, status = is_clean(project_path)
    if not clean:
        msg = f"repo dirty — commit or stash first:\n{status}"
        queue.mark_failed(tid, msg)
        logger.warning("worker task %d: %s", tid, msg)
        return False
    if branch_exists(project_path, prior):
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
    logger.info(
        "worker task %d: prior branch %s missing — creating fresh", tid, prior,
    )
    return _new_branch(queue, task, project_path)


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

Note: `_reuse_or_recreate` calls `is_clean` *before* `branch_exists`. This implements reviewer's "dirty check on reused branches for mutating follow-ups". If `branch_exists` returned False, `_new_branch` will run a second `is_clean` — that's a redundant call but cheap (it's a fork+exec of `git status --porcelain`); the alternative is threading the cleanliness flag through, which couples the two helpers. Keep it simple.

- [ ] **Step 6: Run tests**

Run: `.venv/bin/pytest tests/test_branch_prep.py tests/test_git_ops.py tests/test_project_worker.py -v`
Expected: all PASS.

Run: `.venv/bin/pytest tests/ -q`
Expected: **1267 passed** (1252 + ~15 new — exact count depends on git_ops test breakdown).

Run: `scripts/check-line-limit.sh`
Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add src/branch_prep.py src/git_ops.py tests/test_branch_prep.py tests/test_git_ops.py tests/test_project_worker.py
git commit -m "feat(worker): branch_prep matrix — skip non-mutating, reuse prior branch"
```

---

### Task 9: `apply_reply` resolves prior task via `outbound_emails.task_id` and reuses its branch

**Files:**
- Modify: `src/reply_router.py:60-91` (rework `apply_reply` to look up prior task, classify, enqueue with branch_name + mutates_repo)
- Modify: `src/reply_router.py:1-21` (imports)
- Modify: `src/chat_handlers.py:115-130` (pass the inbound email's In-Reply-To header through to `apply_reply` so it can do the lookup)
- Modify: `tests/test_reply_router.py` (adapt the fake task queue; existing tests must still pass with the new optional arg)
- Create: `tests/test_apply_reply_branch_reuse.py`

This is the central behavioral change of the plan. Read it twice before implementing.

#### Signature changes

`apply_reply` currently takes:
```python
def apply_reply(
    chat_db, task_queue, worker_manager, *,
    agent_name: str, original_message_id: int,
    body: str, allowed_base: str,
) -> tuple[str, str]:
```

It must additionally accept:
- `original_email_message_id: str = ""` — the SMTP `In-Reply-To` header value the user's reply carried. We use it to walk to `outbound_emails.task_id` → prior task → prior `branch_name`.

`chat_handlers._handle_reply` (`src/chat_handlers.py:115-130`) already has the inbound `message` object; pass `message.get("In-Reply-To", "").strip()` through.

#### Lookup chain

```
inbound message
  → In-Reply-To header
  → chat_db.find_outbound_email(header)
  → outbound row has task_id?
       yes → chat_db (via task_queue.get) → prior task → branch_name + mutates_repo
       no  → fall through to current behavior (no prior)
```

`outbound_emails.task_id` populated as of Task 6 will start landing only after deploy; older rows are NULL, in which case we fall through to today's behavior (new branch). That's the desired no-regression property.

- [ ] **Step 1: Write the failing tests**

`tests/test_apply_reply_branch_reuse.py`:

```python
"""Tests for the new branch-reuse path in src/reply_router.apply_reply.

The lookup chain is: inbound In-Reply-To → outbound_emails.task_id →
prior tasks row → branch_name + mutates_repo → new enqueue carries the
prior branch_name so the worker's branch_prep reuses it."""
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


def _project_dir(tmp_path):
    p = tmp_path / "p"
    p.mkdir()
    return str(p.resolve())


def _seed_prior_task(db, tq, project_path, branch_name, mutating=True):
    """Insert a completed prior task + a relayed outbound email pointing
    to it. Returns the SMTP Message-ID we'd see as In-Reply-To on a
    follow-up reply."""
    tid = tq.enqueue(
        project_path, "implement X",
        branch_name=branch_name,
        mutates_repo=mutating,
        origin_message_id="<orig@x>",
        origin_from="user@example.org",
    )
    tq.mark_done(tid)
    db.insert_message("agent-p", "user", "done", "notify", task_id=tid)
    out_id = "<sent-1@x>"
    db.record_outbound_email(
        out_id, kind="result", sender_agent="agent-p", task_id=tid,
    )
    return tid, out_id


class TestBranchReuseFromOutbound:
    def test_reuses_prior_branch_for_mutating_followup(
        self, db, tq, tmp_path,
    ):
        proj = _project_dir(tmp_path)
        db.register_agent("agent-p", proj)
        prior_id, out_id = _seed_prior_task(
            db, tq, proj, "claude/task-17-fix-bus", mutating=True,
        )
        # The user replies "also add docs" → mutating, must reuse branch.
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
        # New task created, carrying the prior branch_name + mutating=True
        new_id = max(
            r["id"] for r in tq._conn.execute("SELECT id FROM tasks").fetchall()
        )
        new = tq.get(new_id)
        assert new["branch_name"] == "claude/task-17-fix-bus"
        assert new["mutates_repo"] == 1
        # ACK still surfaces the (reused) branch name
        assert "claude/task-17-fix-bus" in ack

    def test_read_only_followup_after_mutating_task_reuses_branch(
        self, db, tq, tmp_path,
    ):
        """Reviewer's specific case. The prior task mutated; the follow-up
        is a question. We still reuse the branch (so 'what did you change?'
        runs in the right tree), but the new task is read-only."""
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
        new_id = max(
            r["id"] for r in tq._conn.execute("SELECT id FROM tasks").fetchall()
        )
        new = tq.get(new_id)
        assert new["branch_name"] == "claude/task-17-fix-bus"  # reused
        assert new["mutates_repo"] == 0  # classified read-only

    def test_no_outbound_match_falls_through_to_today(
        self, db, tq, tmp_path,
    ):
        """Pre-deploy outbound rows have no task_id. Reply must still queue."""
        proj = _project_dir(tmp_path)
        db.register_agent("agent-p", proj)
        original = db.insert_message("agent-p", "user", "done", "notify")
        ack, _tag = apply_reply(
            db, tq, _StubWM(),
            agent_name="agent-p", original_message_id=original["id"],
            body="follow up",
            allowed_base=str(tmp_path),
            original_email_message_id="<never-sent@x>",
        )
        new_id = max(
            r["id"] for r in tq._conn.execute("SELECT id FROM tasks").fetchall()
        )
        new = tq.get(new_id)
        assert new["branch_name"] is None  # today's behavior


class TestClassifierIntegration:
    def test_mutating_body_stamps_true_when_no_prior(
        self, db, tq, tmp_path,
    ):
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
        new = tq.latest_task(proj)
        assert new["mutates_repo"] == 1

    def test_read_only_body_stamps_false_when_no_prior(
        self, db, tq, tmp_path,
    ):
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
        new = tq.latest_task(proj)
        assert new["mutates_repo"] == 0

    def test_ambiguous_body_stays_null_when_zero_signal(
        self, db, tq, tmp_path,
    ):
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
        new = tq.latest_task(proj)
        assert new["mutates_repo"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_apply_reply_branch_reuse.py -v`
Expected: FAIL — `apply_reply()` has no `original_email_message_id` kwarg.

- [ ] **Step 3: Update `apply_reply` in `src/reply_router.py`**

Replace the imports + body in `src/reply_router.py`:

```python
"""Reply sub-classification + branch-reuse for email follow-ups.

Three routes (unchanged):
- reply_to_ask: original was a chat_ask → goes on the bus so the
  blocking chat_ask returns.
- reply_to_project: agent has a valid project_path under CLAUDE_CWD →
  queue the reply body as a task and ensure a worker is running.
- reply_bus_only: neither of the above → fall back to bus-only.

Branch-reuse layer (new): when the user replies to a previous result
email, we walk In-Reply-To → outbound_emails.task_id → prior task to
get the prior branch_name. The new task carries that branch_name so
src.branch_prep.prepare_branch reuses it instead of forking a fresh
branch each time. mutates_repo is classified from the reply body so
read-only follow-ups skip the dirty check.
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


def _prior_branch(chat_db, task_queue, in_reply_to_header: str) -> str:
    """Walk inbound In-Reply-To → outbound_emails.task_id → tasks.branch_name.

    Returns "" when any link is missing — caller falls back to fresh branch."""
    if not in_reply_to_header or task_queue is None:
        return ""
    outbound = chat_db.find_outbound_email(in_reply_to_header)
    if not outbound or not outbound.get("task_id"):
        return ""
    prior = task_queue.get(outbound["task_id"])
    if not prior:
        return ""
    return (prior.get("branch_name") or "")


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
        branch = prior_branch or task_branch_name(task_id, body)
        return (
            f"Queued as task #{task_id} for {agent_name} on branch "
            f"`{branch}` (worker pid {worker_pid}).",
            f"Queued #{task_id}",
        )
    if decision.route == "ask":
        return (
            f"Answer delivered to {agent_name} (was waiting on a question).",
            "Answer",
        )
    return (f"Delivered to {agent_name} on the chat bus.", "Delivered")
```

- [ ] **Step 4: Thread the header through `chat_handlers._handle_reply`**

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

The existing `_FakeTaskQueue` accepts `enqueue(self, path, body, priority=0)` — that signature breaks when `apply_reply` passes `branch_name=` and `mutates_repo=`. Update it:

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

Adjust the existing assertion `assert tq.enqueued == [(proj, "also add docs", 0)]` to the new tuple shape:

```python
        assert tq.enqueued == [
            (proj, "also add docs", 0, "", True),  # 'also' is no signal, 'add' is mutating
        ]
```

Actually `classify_mutation("also add docs")` — `add` is in `_MUTATING`, so it returns True. The tuple should reflect `mutates_repo=True`. Confirm against `src/mutation_classifier.py` before committing.

- [ ] **Step 6: Run the full reply-router test surface**

Run: `.venv/bin/pytest tests/test_reply_router.py tests/test_apply_reply_branch_reuse.py -v`
Expected: all PASS.

Run: `.venv/bin/pytest tests/ -q`
Expected: **~1283 passed**.

- [ ] **Step 7: Commit**

```bash
git add src/reply_router.py src/chat_handlers.py tests/test_reply_router.py tests/test_apply_reply_branch_reuse.py
git commit -m "feat(reply): walk outbound_emails.task_id to reuse prior branch on follow-ups"
```

---

### Task 10: `chat_ask` outbound → `outbound_emails.task_id` → reply lookup (reviewer test 2)

This test is the end-to-end version of Task 9 for the chat_ask path. Task 6 already wired `task_id` into `relay_outbound_messages`, so the ingredient is there; this test pins the full chain.

**Files:**
- Modify: `tests/test_apply_reply_branch_reuse.py` (append a class)

- [ ] **Step 1: Append the failing end-to-end test**

```python
class TestChatAskRoundTrip:
    """chat_ask emits a bus message with task_id; the relay turns that into
    an SMTP send recorded with outbound_emails.task_id; a user reply on
    that thread resolves back to the prior task."""

    def test_ask_task_id_round_trips_to_branch_reuse(
        self, db, tq, tmp_path, mocker,
    ):
        from src.chat_relay import relay_outbound_messages

        proj = _project_dir(tmp_path)
        db.register_agent("agent-p", proj)
        prior = tq.enqueue(
            proj, "implement X",
            branch_name="claude/task-9-implement-x",
            mutates_repo=True,
            origin_message_id="<orig@x>", origin_from="user@example.org",
        )
        # Simulate the agent's chat_ask: insert a bus message with task_id.
        db.insert_message(
            "agent-p", "user", "should I push?", "ask", task_id=prior,
        )
        mocker.patch(
            "src.chat_relay.send_reply", return_value="<ask-sent@x>",
        )
        config = {
            "smtp_host": "smtp.example.test", "smtp_port": 465,
            "username": "agent@example.test", "password": "x",
            "email_domain": "example.test",
        }
        relay_outbound_messages(config, db)

        # User now replies on that thread — apply_reply must see task_id 9.
        original = db.insert_message(
            "agent-p", "user", "should I push?", "ask",
        )
        apply_reply(
            db, tq, _StubWM(),
            agent_name="agent-p", original_message_id=original["id"],
            body="yes push and tag",
            allowed_base=str(tmp_path),
            original_email_message_id="<ask-sent@x>",
        )
        new = tq.latest_task(proj)
        assert new["branch_name"] == "claude/task-9-implement-x"
```

- [ ] **Step 2: Run test**

Run: `.venv/bin/pytest tests/test_apply_reply_branch_reuse.py::TestChatAskRoundTrip -v`
Expected: PASS without any code change (Tasks 6 + 9 already wire this end-to-end).

If it fails on `subject_base_for_message` / `recipient_for_message` due to a missing fixture, mock those too — they're not what this test exercises.

Run: `.venv/bin/pytest tests/ -q`
Expected: same as Task 9 + 1 (≈1284).

- [ ] **Step 3: Commit**

```bash
git add tests/test_apply_reply_branch_reuse.py
git commit -m "test: end-to-end chat_ask → outbound_emails.task_id → reply branch reuse"
```

---

### Task 11: `enqueue_task_tool` surface accepts `mutates_repo`

For MCP callers (router-level enqueues, retries from the dashboard) we expose the same hint. The JSON envelope path uses `enqueue_task_tool`, so this is also how envelope tasks get classified — but optionally; default stays NULL.

**Files:**
- Modify: `chat/project_tools.py:41-70`
- Modify: `tests/test_enqueue_task_tool.py` (one new case)

- [ ] **Step 1: Write the failing test**

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

    def test_default_stays_null(self, tq, mgr, tmp_path, mocker):
        (tmp_path / "p").mkdir()
        proc = mocker.MagicMock(pid=1)
        proc.poll.return_value = None
        mocker.patch("src.worker_manager.subprocess.Popen", return_value=proc)
        result = enqueue_task_tool(
            tq, mgr, project="p", body="x", allowed_base=str(tmp_path),
        )
        assert tq.get(result["task_id"])["mutates_repo"] is None
```

- [ ] **Step 2: Run test to verify it fails**

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
    task_id = queue.enqueue(
        resolved, body, priority=_clamp_priority(priority), plan_first=plan_first,
        origin_content_type=origin_content_type,
        origin_message_id=origin_message_id, origin_subject=origin_subject,
        origin_from=origin_from, dispatch_token=dispatch_token,
        origin_envelope_v=origin_envelope_v,
        mutates_repo=mutates_repo,
    )
    return {
        "status": "enqueued",
        "task_id": task_id,
        "worker_pid": worker_pid,
        "planned_branch": task_branch_name(task_id, body),
        "plan_first": plan_first,
    }
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_enqueue_task_tool.py -v`
Expected: all PASS.

Run: `.venv/bin/pytest tests/ -q`
Expected: **~1286 passed**.

- [ ] **Step 5: Commit**

```bash
git add chat/project_tools.py tests/test_enqueue_task_tool.py
git commit -m "feat(mcp): enqueue_task_tool accepts mutates_repo hint"
```

---

### Task 12: Concurrency guard — "only one running task per project/branch" assertion

Reviewer flagged: "branch reuse makes the one-worker-per-project assumption more important." `claim_next` already enforces one running task per project (project_path filter), but branch reuse means a *cancelled* mid-task could leave the branch dirty for the next reuse. We add a defensive log + test that asserts the property.

This task is a sanity test only — no production code change unless the test reveals a bug.

**Files:**
- Create: `tests/test_one_running_per_branch.py`

- [ ] **Step 1: Write the assertion test**

```python
"""Branch reuse strengthens the 'one running task per project' invariant.

If two pending follow-ups both carry the same prior branch_name and
claim_next picks them up out of order, they could end up running on
the same branch concurrently. claim_next already filters on
project_path so per-project workers are serial; this test pins that
invariant explicitly so any future change to the queue is caught."""
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
    # Second claim must return None while first is still 'running'.
    assert tq.claim_next("/p") is None
    # Once first finishes, second becomes claimable.
    tq.mark_done(first["id"])
    second = tq.claim_next("/p")
    assert second["id"] == b


def test_running_task_is_singleton_per_project(tmp_path):
    path = str(tmp_path / "db")
    ChatDB(path)
    tq = TaskQueue(path)
    tq.enqueue("/p", "a")
    tq.enqueue("/p", "b")
    tq.enqueue("/p", "c")
    tq.claim_next("/p")
    running = [
        r for r in tq._conn.execute(
            "SELECT * FROM tasks WHERE project_path='/p' AND status='running'"
        ).fetchall()
    ]
    assert len(running) == 1
```

- [ ] **Step 2: Run test**

Run: `.venv/bin/pytest tests/test_one_running_per_branch.py -v`
Expected: PASS without any code change — `claim_next` already enforces this. Test exists as a regression guard.

Run: `.venv/bin/pytest tests/ -q`
Expected: **~1288 passed**.

- [ ] **Step 3: Commit**

```bash
git add tests/test_one_running_per_branch.py
git commit -m "test: pin one-running-task-per-project invariant for branch reuse"
```

---

### Task 13: README + website doc sweep

Per CLAUDE.md: "Docs follow code — whenever a change alters user-visible behavior, configuration surface, or the test count, update README.md and the website in the same PR."

- [ ] **Step 1: Update `README.md`**

Find the feature/behavior section and add a short paragraph (≤6 lines) under it:

```markdown
### Read-only tasks skip the dirty-repo gate

Tasks classified as obviously read-only (`explain …`, `show …`, `list …`,
plain interrogatives) no longer require a clean working tree and no
longer fork a per-task branch. Mutating tasks still require clean.
Classification is conservative — anything ambiguous (e.g. `also fix
the rest`) is treated as mutating.

### Email follow-ups continue on the same branch

When you reply on a thread that came from a prior task's result, the
follow-up task reuses the prior task's branch. The lookup walks the
SMTP `In-Reply-To` header back through `outbound_emails.task_id`.
Pre-existing rows without that column behave exactly as today.
```

Also bump the test-count badge if README has one. The new count is whatever `.venv/bin/pytest tests/ -q` reports after Task 12 (target ≈1288 — confirm the exact number before committing).

- [ ] **Step 2: Update `website/index.html` and `website/fa/index.html`**

Apply the same two paragraphs (translated) in both files. They live in lockstep per CLAUDE.md.

If the website pages are CSS-driven sections, follow the file's existing structural pattern — don't introduce new section types unless asked.

- [ ] **Step 3: Run the full suite to confirm nothing regressed**

Run: `.venv/bin/pytest tests/ -q`
Expected: identical to Task 12.

- [ ] **Step 4: Commit**

```bash
git add README.md website/index.html website/fa/index.html
git commit -m "docs: dirty-repo gate skips read-only tasks + branch reuse for follow-ups"
```

---

### Task 14: `/simplify` sweep

Per memory `feedback_simplify_when_done`: `/simplify` the working tree before each commit and fold fixups into the same commit. Since this plan accumulates commits, run `/simplify` after Task 13 across all the touched files and amend the most recent commit (or commit a single `style:` follow-up).

- [ ] **Step 1: Run `/simplify` on changed files**

Use the `simplify` skill — pass the list of files touched in this PR. It will return a diff; review and apply.

- [ ] **Step 2: Re-run the full suite**

Run: `.venv/bin/pytest tests/ -q`
Expected: same count.

Run: `scripts/check-line-limit.sh`
Expected: pass.

- [ ] **Step 3: Commit (if any /simplify edits were applied)**

```bash
git add -p  # review every hunk
git commit -m "style: post-simplify cleanup"
```

If `/simplify` found nothing, skip the commit.

---

### Task 15: Coverage check

- [ ] **Step 1: Run pytest with coverage**

Run: `.venv/bin/pytest tests/ --cov=src --cov=chat --cov-report=term-missing`

Expected: 100% coverage on production code. Any untested branch in `src/branch_prep.py`, `src/mutation_classifier.py`, `src/outbound_emails_store.py`, `src/reply_router.py`, `src/task_queue.py`, or `chat/project_tools.py` must get a focused test.

- [ ] **Step 2: Patch any uncovered lines**

If coverage drops below 100% on production code, add tests in the relevant `tests/test_*.py` file targeting the uncovered line numbers. Commit those tests:

```bash
git add tests/
git commit -m "test: lift coverage back to 100% on touched modules"
```

---

### Task 16: Final verification + integration smoke

- [ ] **Step 1: Lint + line check**

Run: `scripts/check-line-limit.sh`
Expected: pass.

- [ ] **Step 2: Confirm migration on a real `claude-chat.db` snapshot**

```bash
cp ~/.local/share/claude-chat/claude-chat.db /tmp/old.db
.venv/bin/python -c "from src.chat_db import ChatDB; ChatDB('/tmp/old.db')"
sqlite3 /tmp/old.db "PRAGMA table_info(tasks)" | grep mutates_repo
sqlite3 /tmp/old.db "PRAGMA table_info(outbound_emails)" | grep task_id
sqlite3 /tmp/old.db "SELECT COUNT(*) FROM tasks WHERE mutates_repo IS NULL"
```

Expected: both new columns present; every existing row has `mutates_repo IS NULL` (i.e. behavior preserved).

If the local DB path differs (check `.env` for `CHAT_DB_PATH`), adapt the copy command.

- [ ] **Step 3: Manual smoke (optional, behind chat_ask to user)**

Send the user one chat_ask describing the deploy plan:

```
About to restart claude-chat with the new migrations. This will sever
existing MCP sessions (per CLAUDE.md). Restart now or wait?
```

Hold for explicit go-ahead before any `systemctl --user restart`.

---

### Task 17: Self-review checkpoint

After all code tasks and before any merge:

- [ ] **Spec coverage:** Walk the reviewer's verdict (top of this plan / inbox msg #1438) line by line. Every adjustment must point at a task. Gaps listed below should be `(none)`.

- [ ] **Placeholder scan:** `grep -rn 'TODO\|FIXME\|XXX' src/branch_prep.py src/mutation_classifier.py src/outbound_emails_store.py src/reply_router.py src/task_queue.py chat/project_tools.py` — must be empty.

- [ ] **Type consistency:** `branch_name: str` is the column type everywhere; `mutates_repo: bool | None` in Python, `INTEGER` (NULL/0/1) on disk. Verify these in `src/task_queue.py` (insert), `src/branch_prep.py` (read), `src/reply_router.py` (write), `chat/project_tools.py` (passthrough).

- [ ] **Name consistency:** `prepare_branch` (not `_prepare_branch`, not `prep_branch`). `classify_mutation` (not `mutation_intent`). `find_outbound_email` (not `get_outbound_email`).

- [ ] **No leftover stale imports:** `grep -n 'from src.git_ops import .*checkout_new_branch' src/project_worker.py` must be empty (it was moved to `branch_prep`).

---

### Task 18: Ready-for-PR

- [ ] **Run final test suite**

Run: `.venv/bin/pytest tests/ -q`
Capture the exact test count for the PR description.

- [ ] **Skim `git log master..HEAD`**

The commit list should read as a clean story:

```
refactor(chat_db): extract OutboundEmailsMixin to free 200-line headroom
feat(schema): add tasks.mutates_repo and outbound_emails.task_id
feat(outbound): record_outbound_email accepts task_id
feat: conservative regex classifier for task body mutation intent
feat(queue): enqueue accepts branch_name and mutates_repo
feat(relay): stamp task_id on outbound emails for reply-walkback
refactor(worker): extract prepare_branch into src/branch_prep.py
feat(worker): branch_prep matrix — skip non-mutating, reuse prior branch
feat(reply): walk outbound_emails.task_id to reuse prior branch on follow-ups
test: end-to-end chat_ask → outbound_emails.task_id → reply branch reuse
feat(mcp): enqueue_task_tool accepts mutates_repo hint
test: pin one-running-task-per-project invariant for branch reuse
docs: dirty-repo gate skips read-only tasks + branch reuse for follow-ups
style: post-simplify cleanup  (only if applied)
test: lift coverage back to 100% on touched modules  (only if applied)
```

- [ ] **Hand back to the user**

Report the final test count, the list of new/modified files, and the proposed PR title:

> `fix: skip dirty-repo gate for read-only tasks; reuse prior branch on email follow-ups`

Ask whether to push and open the PR with `gh pr create`.

---

## Risk notes for the implementer

1. **Don't restart `claude-chat` to "test" the migration.** Per CLAUDE.md operational notes, that severs live MCP sessions and breaks the dashboard for every running agent. The migration runs at `ChatDB.__init__` time — Task 16's smoke covers the upgrade path on a copy of the DB.

2. **The `tasks_dispatch_token_idx` index in MIGRATIONS depends on the `dispatch_token` ALTER landing first.** Ordering matters — append new migrations to the end of the list, never insert. Task 2 does the right thing.

3. **`apply_reply`'s fake `_FakeTaskQueue` in `tests/test_reply_router.py` is the kind of fixture that silently swallows new kwargs.** Task 9 step 5 is the most likely place a regression hides — verify the existing assertions still match after the tuple shape changes.

4. **The classifier is intentionally dumb.** Don't grow it into an LLM call without a separate spec. The whole safety story is "biased to mutating; NULL passes through to today's behavior." Adding nuance erodes that.

5. **`current_branch` exists in `src/git_ops.py` already.** If the reuse path needs to check whether we're already on the right branch and skip the checkout, add an optimization in a follow-up PR — not in this one. Keep this PR's diff focused.
