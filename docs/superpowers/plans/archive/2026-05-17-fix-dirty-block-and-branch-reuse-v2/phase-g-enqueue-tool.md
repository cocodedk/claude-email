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
