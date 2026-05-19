"""branch_prep matrix — already on prior branch (safe-to-continue cell)."""
import pytest

from src import branch_prep

from tests._branch_prep_helpers import _task, queue  # noqa: F401


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
