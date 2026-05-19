"""branch_prep matrix — non-git and read-only-no-prior cells."""
from src import branch_prep

from tests._branch_prep_helpers import _task, queue  # noqa: F401


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
