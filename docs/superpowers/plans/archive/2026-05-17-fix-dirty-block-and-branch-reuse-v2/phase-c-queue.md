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
