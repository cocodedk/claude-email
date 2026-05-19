"""branch_prep matrix — defense-in-depth edge cases."""
from src import branch_prep

from tests._branch_prep_helpers import _task, queue  # noqa: F401


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
