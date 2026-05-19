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
from src import branch_prep, project_worker

from tests._branch_prep_helpers import _task


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
