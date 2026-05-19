"""Tests for src/project_worker.py — branch preparation around run_task."""
import pytest
from src.project_worker import run_task

from tests._project_worker_helpers import tq, cfg, _mock_proc  # noqa: F401


@pytest.fixture(autouse=True)
def _skip_branch_prep(mocker):
    """Default: treat project_path as non-git so run_task skips branch work.
    Tests that exercise the branch dance override src.branch_prep.is_git_repo
    themselves."""
    mocker.patch("src.branch_prep.is_git_repo", return_value=False)


class TestBranchPreparation:
    def test_non_git_skips_branch_and_runs(self, tq, cfg, mocker):
        """is_git_repo=False → no branch, claude still runs."""
        tid = tq.enqueue(cfg.project_path, "task")
        claimed = tq.claim_next(cfg.project_path)
        proc = _mock_proc(mocker, pid=9, returncode=0)
        popen = mocker.patch("src.project_worker.subprocess.Popen", return_value=proc)
        run_task(tq, claimed, cfg)
        assert tq.get(tid)["status"] == "done"
        assert tq.get(tid)["branch_name"] is None
        popen.assert_called_once()

    def test_dirty_repo_fails_task_without_running(self, tq, cfg, mocker):
        mocker.patch("src.branch_prep.is_git_repo", return_value=True)
        mocker.patch(
            "src.branch_prep.is_clean",
            return_value=(False, " M file.py"),
        )
        popen = mocker.patch("src.project_worker.subprocess.Popen")
        tid = tq.enqueue(cfg.project_path, "won't run")
        claimed = tq.claim_next(cfg.project_path)
        run_task(tq, claimed, cfg)
        row = tq.get(tid)
        assert row["status"] == "failed"
        assert "dirty" in row["error_text"].lower()
        popen.assert_not_called()

    def test_clean_repo_creates_branch_then_runs(self, tq, cfg, mocker):
        mocker.patch("src.branch_prep.is_git_repo", return_value=True)
        mocker.patch("src.branch_prep.is_clean", return_value=(True, ""))
        checkout = mocker.patch(
            "src.branch_prep.checkout_new_branch",
            return_value=(True, ""),
        )
        proc = _mock_proc(mocker, pid=9, returncode=0)
        mocker.patch("src.project_worker.subprocess.Popen", return_value=proc)
        tid = tq.enqueue(cfg.project_path, "refactor config")
        claimed = tq.claim_next(cfg.project_path)
        run_task(tq, claimed, cfg)
        assert tq.get(tid)["branch_name"] == f"claude/task-{tid}-refactor-config"
        assert tq.get(tid)["status"] == "done"
        checkout.assert_called_once()

    def test_checkout_failure_fails_task(self, tq, cfg, mocker):
        mocker.patch("src.branch_prep.is_git_repo", return_value=True)
        mocker.patch("src.branch_prep.is_clean", return_value=(True, ""))
        mocker.patch(
            "src.branch_prep.checkout_new_branch",
            return_value=(False, "fatal: branch exists"),
        )
        popen = mocker.patch("src.project_worker.subprocess.Popen")
        tid = tq.enqueue(cfg.project_path, "x")
        claimed = tq.claim_next(cfg.project_path)
        run_task(tq, claimed, cfg)
        row = tq.get(tid)
        assert row["status"] == "failed"
        assert "branch" in row["error_text"].lower()
        popen.assert_not_called()
