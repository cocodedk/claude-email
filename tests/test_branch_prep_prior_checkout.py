"""branch_prep matrix — checkout-existing path (success + failure)."""
from src import branch_prep

from tests._branch_prep_helpers import _task, queue  # noqa: F401


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
