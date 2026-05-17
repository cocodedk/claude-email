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
    mocker.patch("src.project_worker.log_task_finished")
    mocker.patch("src.project_worker.notify_task_done")
    queue = mocker.MagicMock()
    queue.get.return_value = {"id": 1, "status": "failed", "body": "x"}
    claimed = {"id": 1, "body": "x", "branch_name": None, "mutates_repo": None}
    cfg = project_worker.WorkerConfig(
        project_path=str(tmp_path), db_path=str(tmp_path / "db"),
        claude_bin="claude", mcp_config=str(tmp_path / ".mcp.json"),
    )
    project_worker.run_task(queue, claimed, cfg)
    sentinel.assert_called_once()
