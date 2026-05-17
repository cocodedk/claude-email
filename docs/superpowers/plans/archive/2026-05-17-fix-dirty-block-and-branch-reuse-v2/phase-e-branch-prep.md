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
