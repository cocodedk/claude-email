"""Tests for chat.tools.enqueue_task_tool — router-facing MCP tool."""
import pytest
from src.chat_db import ChatDB
from src.task_queue import TaskQueue
from src.worker_manager import WorkerManager
from chat.tools import enqueue_task_tool


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
    mocker.patch(
        "src.worker_manager._find_external_worker_pid", return_value=None,
    )
    return WorkerManager(
        db_path=db_path, project_root=str(tmp_path),
        python_bin="/usr/bin/python3",
    )


class TestEnqueueTaskTool:
    def test_happy_path_spawns_worker_and_returns_ids(self, tq, mgr, tmp_path, mocker):
        (tmp_path / "p").mkdir()
        proc = mocker.MagicMock(pid=777)
        proc.poll.return_value = None
        mocker.patch("src.worker_manager.subprocess.Popen", return_value=proc)
        result = enqueue_task_tool(
            tq, mgr,
            project="p", body="write tests",
            allowed_base=str(tmp_path),
        )
        assert result["status"] == "enqueued"
        assert result["worker_pid"] == 777
        assert tq.get(result["task_id"])["body"] == "write tests"
        assert tq.get(result["task_id"])["project_path"] == str((tmp_path / "p").resolve())
        # planned_branch surfaces the expected branch name to the router
        assert result["planned_branch"].startswith("claude/task-")
        assert result["planned_branch"].endswith("write-tests")

    def test_accepts_absolute_path(self, tq, mgr, tmp_path, mocker):
        (tmp_path / "p").mkdir()
        proc = mocker.MagicMock(pid=1)
        proc.poll.return_value = None
        mocker.patch("src.worker_manager.subprocess.Popen", return_value=proc)
        result = enqueue_task_tool(
            tq, mgr, project=str(tmp_path / "p"), body="x",
            allowed_base=str(tmp_path),
        )
        assert result["status"] == "enqueued"

    def test_path_outside_base_rejected(self, tq, mgr, tmp_path):
        outside = tmp_path.parent / "outside"
        outside.mkdir(exist_ok=True)
        try:
            result = enqueue_task_tool(
                tq, mgr, project=str(outside), body="x",
                allowed_base=str(tmp_path),
            )
            assert "error" in result
        finally:
            outside.rmdir()

    def test_nonexistent_path_rejected(self, tq, mgr, tmp_path):
        result = enqueue_task_tool(
            tq, mgr, project="never-made", body="x",
            allowed_base=str(tmp_path),
        )
        assert "error" in result

    def test_missing_allowed_base_rejected(self, tq, mgr):
        result = enqueue_task_tool(
            tq, mgr, project="p", body="x", allowed_base="",
        )
        assert "error" in result

    def test_worker_manager_error_bubbles(self, tq, mgr, tmp_path, mocker):
        (tmp_path / "p").mkdir()
        mocker.patch.object(mgr, "ensure_worker", side_effect=ValueError("boom"))
        result = enqueue_task_tool(
            tq, mgr, project="p", body="x",
            allowed_base=str(tmp_path),
        )
        assert result == {"error": "boom", "error_code": "internal"}

    def test_priority_preserved(self, tq, mgr, tmp_path, mocker):
        (tmp_path / "p").mkdir()
        proc = mocker.MagicMock(pid=1)
        proc.poll.return_value = None
        mocker.patch("src.worker_manager.subprocess.Popen", return_value=proc)
        result = enqueue_task_tool(
            tq, mgr, project="p", body="help", priority=10,
            allowed_base=str(tmp_path),
        )
        assert tq.get(result["task_id"])["priority"] == 10


class TestPriorityBounds:
    def test_priority_above_max_is_clamped(self, tq, mgr, tmp_path, mocker):
        (tmp_path / "p").mkdir()
        proc = mocker.MagicMock(pid=1)
        proc.poll.return_value = None
        mocker.patch("src.worker_manager.subprocess.Popen", return_value=proc)
        result = enqueue_task_tool(
            tq, mgr, project="p", body="x", priority=9999,
            allowed_base=str(tmp_path),
        )
        assert tq.get(result["task_id"])["priority"] == 10

    def test_priority_below_min_is_clamped(self, tq, mgr, tmp_path, mocker):
        (tmp_path / "p").mkdir()
        proc = mocker.MagicMock(pid=1)
        proc.poll.return_value = None
        mocker.patch("src.worker_manager.subprocess.Popen", return_value=proc)
        result = enqueue_task_tool(
            tq, mgr, project="p", body="x", priority=-5,
            allowed_base=str(tmp_path),
        )
        assert tq.get(result["task_id"])["priority"] == 0


class TestOriginSubject:
    def test_origin_subject_persisted_on_row(self, tq, mgr, tmp_path, mocker):
        (tmp_path / "p").mkdir()
        proc = mocker.MagicMock(pid=1)
        proc.poll.return_value = None
        mocker.patch("src.worker_manager.subprocess.Popen", return_value=proc)
        result = enqueue_task_tool(
            tq, mgr, project="p", body="x",
            allowed_base=str(tmp_path),
            origin_subject="[test-0042] hello",
        )
        assert tq.get(result["task_id"])["origin_subject"] == "[test-0042] hello"


class TestPlanFirst:
    def test_plan_first_flag_persisted_on_row(self, tq, mgr, tmp_path, mocker):
        (tmp_path / "p").mkdir()
        proc = mocker.MagicMock(pid=1)
        proc.poll.return_value = None
        mocker.patch("src.worker_manager.subprocess.Popen", return_value=proc)
        result = enqueue_task_tool(
            tq, mgr, project="p", body="review the architecture",
            allowed_base=str(tmp_path), plan_first=True,
        )
        assert result["plan_first"] is True
        assert tq.get(result["task_id"])["plan_first"] == 1

    def test_plan_first_default_false(self, tq, mgr, tmp_path, mocker):
        (tmp_path / "p").mkdir()
        proc = mocker.MagicMock(pid=1)
        proc.poll.return_value = None
        mocker.patch("src.worker_manager.subprocess.Popen", return_value=proc)
        result = enqueue_task_tool(
            tq, mgr, project="p", body="add a test",
            allowed_base=str(tmp_path),
        )
        assert result["plan_first"] is False
        assert tq.get(result["task_id"])["plan_first"] == 0


class TestHighPriorityJumpsQueue:
    def test_higher_priority_claimed_first(self, tq, mgr, tmp_path, mocker):
        (tmp_path / "p").mkdir()
        proc = mocker.MagicMock(pid=1)
        proc.poll.return_value = None
        mocker.patch("src.worker_manager.subprocess.Popen", return_value=proc)
        low = enqueue_task_tool(
            tq, mgr, project="p", body="regular",
            allowed_base=str(tmp_path),
        )
        high = enqueue_task_tool(
            tq, mgr, project="p", body="help!", priority=10,
            allowed_base=str(tmp_path),
        )
        first = tq.claim_next(str((tmp_path / "p").resolve()))
        assert first["id"] == high["task_id"]
        # Queue has no concurrency cap itself — the worker is what enforces
        # one-at-a-time. So the next claim_next returns the low-priority task.
        second = tq.claim_next(str((tmp_path / "p").resolve()))
        assert second["id"] == low["task_id"]
