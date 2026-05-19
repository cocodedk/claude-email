"""branch_prep matrix — missing-prior fallback and dirty-switch failure."""
import pytest

from src import branch_prep

from tests._branch_prep_helpers import _task, queue  # noqa: F401


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
