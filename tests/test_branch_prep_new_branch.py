"""branch_prep matrix — no-prior-branch path (clean/dirty/failure)."""
from src import branch_prep

from tests._branch_prep_helpers import _task, queue  # noqa: F401


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
