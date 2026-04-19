"""Tests for chat.tools.retry_task_tool — retry a previous task."""
import pytest
from src.chat_db import ChatDB
from src.task_queue import TaskQueue
from src.worker_manager import WorkerManager
from chat.tools import retry_task_tool


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "db")
    ChatDB(path)
    return path


@pytest.fixture
def tq(db_path):
    return TaskQueue(db_path)


@pytest.fixture
def mgr(db_path, tmp_path, mocker):
    mocker.patch("src.worker_manager.is_alive", return_value=True)
    mocker.patch("src.worker_manager._find_external_worker_pid", return_value=None)
    return WorkerManager(
        db_path=db_path, project_root=str(tmp_path), python_bin="/usr/bin/python3",
    )


def _terminal_task(tq, path, body, status="failed"):
    tid = tq.enqueue(path, body)
    tq.claim_next(path)
    if status == "done":
        tq.mark_done(tid)
    elif status == "failed":
        tq.mark_failed(tid, "boom")
    elif status == "cancelled":
        tq.cancel(tid)
    return tid


class TestRetryTaskTool:
    def test_retries_failed_task_with_same_body(self, tq, mgr, tmp_path, mocker):
        (tmp_path / "p").mkdir()
        proj = str((tmp_path / "p").resolve())
        tid = _terminal_task(tq, proj, "original body", status="failed")
        proc = mocker.MagicMock(pid=111)
        proc.poll.return_value = None
        mocker.patch("src.worker_manager.subprocess.Popen", return_value=proc)
        result = retry_task_tool(tq, mgr, task_id=tid)
        assert result["status"] == "retried"
        assert result["retry_of"] == tid
        new = tq.get(result["new_task_id"])
        assert new["body"] == "original body"
        assert new["retry_of"] == tid
        assert new["project_path"] == proj

    def test_retries_with_refinement(self, tq, mgr, tmp_path, mocker):
        (tmp_path / "p").mkdir()
        proj = str((tmp_path / "p").resolve())
        tid = _terminal_task(tq, proj, "orig", status="done")
        proc = mocker.MagicMock(pid=1)
        proc.poll.return_value = None
        mocker.patch("src.worker_manager.subprocess.Popen", return_value=proc)
        result = retry_task_tool(tq, mgr, task_id=tid, new_body="also add tests")
        new = tq.get(result["new_task_id"])
        assert new["body"] == "also add tests"

    def test_rejects_running_task(self, tq, mgr, tmp_path):
        (tmp_path / "p").mkdir()
        proj = str((tmp_path / "p").resolve())
        tid = tq.enqueue(proj, "still going")
        tq.claim_next(proj)  # status=running
        result = retry_task_tool(tq, mgr, task_id=tid)
        assert "error" in result
        assert "running" in result["error"]

    def test_rejects_pending_task(self, tq, mgr, tmp_path):
        (tmp_path / "p").mkdir()
        proj = str((tmp_path / "p").resolve())
        tid = tq.enqueue(proj, "not yet")
        result = retry_task_tool(tq, mgr, task_id=tid)
        assert "error" in result
        assert "pending" in result["error"]

    def test_rejects_unknown_task(self, tq, mgr):
        result = retry_task_tool(tq, mgr, task_id=9999)
        assert "error" in result
        assert "not found" in result["error"]

    def test_preserves_priority(self, tq, mgr, tmp_path, mocker):
        (tmp_path / "p").mkdir()
        proj = str((tmp_path / "p").resolve())
        tid = tq.enqueue(proj, "urgent", priority=10)
        tq.claim_next(proj)
        tq.mark_failed(tid, "x")
        proc = mocker.MagicMock(pid=1)
        proc.poll.return_value = None
        mocker.patch("src.worker_manager.subprocess.Popen", return_value=proc)
        result = retry_task_tool(tq, mgr, task_id=tid)
        assert tq.get(result["new_task_id"])["priority"] == 10

    def test_worker_manager_failure_bubbles(self, tq, mgr, tmp_path, mocker):
        (tmp_path / "p").mkdir()
        proj = str((tmp_path / "p").resolve())
        tid = _terminal_task(tq, proj, "x", status="failed")
        mocker.patch.object(mgr, "ensure_worker", side_effect=ValueError("boom"))
        result = retry_task_tool(tq, mgr, task_id=tid)
        assert result == {"error": "boom"}
