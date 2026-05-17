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
    q.get.return_value = {"id": 1, "status": "failed", "body": "x"}
    mocker.patch("src.project_worker.log_task_finished")
    mocker.patch("src.project_worker.notify_task_done")
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

    def test_first_time_read_only_question_never_creates_branch(self, queue, mocker):
        """Named blocker: a first-time question like 'explain the schema'
        must run through prepare_branch without dirty check, without
        branch creation, and without recording a branch_name. Pins the
        rule inside the matrix tests so it is visible here, not only in
        the MCP-level tests in tests/test_enqueue_task_tool.py."""
        mocker.patch("src.branch_prep.is_git_repo", return_value=True)
        is_clean = mocker.patch("src.branch_prep.is_clean")
        checkout_new = mocker.patch("src.branch_prep.checkout_new_branch")

        ok = branch_prep.prepare_branch(
            queue,
            _task(body="explain the schema", mutates_repo=False),
            "/tmp",
        )

        assert ok is True
        is_clean.assert_not_called()
        checkout_new.assert_not_called()
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
        co_new.assert_not_called()
        co_existing.assert_not_called()
        is_clean.assert_not_called()
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


class TestPriorBranchCheckoutFailure:
    """Clean repo, branch exists, but the checkout subprocess errors —
    propagate the failure to the queue. Covers the unhappy path of
    checkout_existing_branch inside _handle_prior."""

    def test_existing_checkout_failure_marks_failed(self, queue, mocker):
        mocker.patch("src.branch_prep.is_git_repo", return_value=True)
        mocker.patch(
            "src.branch_prep.current_branch", return_value="main",
        )
        mocker.patch("src.branch_prep.is_clean", return_value=(True, ""))
        mocker.patch("src.branch_prep.branch_exists", return_value=True)
        mocker.patch(
            "src.branch_prep.checkout_existing_branch",
            return_value=(False, "permission denied"),
        )
        ok = branch_prep.prepare_branch(
            queue,
            _task(branch_name="claude/task-17-fix", mutates_repo=True),
            "/tmp",
        )
        assert ok is False
        queue.mark_failed.assert_called_once()
        args = queue.mark_failed.call_args.args
        assert "claude/task-17-fix" in args[1]
        assert "permission denied" in args[1]


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
