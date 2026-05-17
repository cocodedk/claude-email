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
