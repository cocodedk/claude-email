"""Tests for enqueue_task_tool's mutates_repo auto-classification and
honest planned_branch behavior.

Split from test_enqueue_task_tool.py to keep both files under the
200-line cap.
"""
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


class TestMutatesRepoHint:
    def test_explicit_false_persists(self, tq, mgr, tmp_path, mocker):
        (tmp_path / "p").mkdir()
        proc = mocker.MagicMock(pid=1)
        proc.poll.return_value = None
        mocker.patch("src.worker_manager.subprocess.Popen", return_value=proc)
        result = enqueue_task_tool(
            tq, mgr, project="p", body="show me the schema",
            allowed_base=str(tmp_path),
            mutates_repo=False,
        )
        assert tq.get(result["task_id"])["mutates_repo"] == 0

    def test_default_auto_classifies_read_only(
        self, tq, mgr, tmp_path, mocker,
    ):
        """v2 reviewer blocker 3: first-time tasks must classify too,
        not just replies. 'explain' is read-only."""
        (tmp_path / "p").mkdir()
        proc = mocker.MagicMock(pid=1)
        proc.poll.return_value = None
        mocker.patch("src.worker_manager.subprocess.Popen", return_value=proc)
        result = enqueue_task_tool(
            tq, mgr, project="p", body="explain the schema",
            allowed_base=str(tmp_path),
        )
        assert tq.get(result["task_id"])["mutates_repo"] == 0

    def test_default_auto_classifies_mutating(
        self, tq, mgr, tmp_path, mocker,
    ):
        (tmp_path / "p").mkdir()
        proc = mocker.MagicMock(pid=1)
        proc.poll.return_value = None
        mocker.patch("src.worker_manager.subprocess.Popen", return_value=proc)
        result = enqueue_task_tool(
            tq, mgr, project="p", body="fix the relay",
            allowed_base=str(tmp_path),
        )
        assert tq.get(result["task_id"])["mutates_repo"] == 1

    def test_empty_body_stays_null(self, tq, mgr, tmp_path, mocker):
        (tmp_path / "p").mkdir()
        proc = mocker.MagicMock(pid=1)
        proc.poll.return_value = None
        mocker.patch("src.worker_manager.subprocess.Popen", return_value=proc)
        result = enqueue_task_tool(
            tq, mgr, project="p", body="",
            allowed_base=str(tmp_path),
        )
        assert tq.get(result["task_id"])["mutates_repo"] is None

    def test_explicit_hint_overrides_classifier(
        self, tq, mgr, tmp_path, mocker,
    ):
        """Caller's explicit hint wins. 'fix the bus' classifies as
        mutating, but mutates_repo=False from the caller stands."""
        (tmp_path / "p").mkdir()
        proc = mocker.MagicMock(pid=1)
        proc.poll.return_value = None
        mocker.patch("src.worker_manager.subprocess.Popen", return_value=proc)
        result = enqueue_task_tool(
            tq, mgr, project="p", body="fix the bus",
            allowed_base=str(tmp_path),
            mutates_repo=False,
        )
        assert tq.get(result["task_id"])["mutates_repo"] == 0


class TestPlannedBranchHonesty:
    """The planned_branch field must be empty for read-only tasks since
    branch_prep will not create a branch in that case."""

    def test_read_only_returns_empty_planned_branch(
        self, tq, mgr, tmp_path, mocker,
    ):
        (tmp_path / "p").mkdir()
        proc = mocker.MagicMock(pid=1)
        proc.poll.return_value = None
        mocker.patch("src.worker_manager.subprocess.Popen", return_value=proc)
        result = enqueue_task_tool(
            tq, mgr, project="p", body="explain the schema",
            allowed_base=str(tmp_path),
        )
        assert not result.get("planned_branch")

    def test_mutating_returns_real_planned_branch(
        self, tq, mgr, tmp_path, mocker,
    ):
        (tmp_path / "p").mkdir()
        proc = mocker.MagicMock(pid=1)
        proc.poll.return_value = None
        mocker.patch("src.worker_manager.subprocess.Popen", return_value=proc)
        result = enqueue_task_tool(
            tq, mgr, project="p", body="implement X",
            allowed_base=str(tmp_path),
        )
        assert result["planned_branch"].startswith("claude/task-")
        assert result["planned_branch"].endswith("implement-x")

    def test_first_time_read_only_question_does_not_create_branch(
        self, tq, mgr, tmp_path, mocker,
    ):
        """User-facing blocker: a harmless first-time question like
        'explain the schema' must not fork a branch."""
        from src import branch_prep

        (tmp_path / "p").mkdir()
        proc = mocker.MagicMock(pid=1)
        proc.poll.return_value = None
        mocker.patch("src.worker_manager.subprocess.Popen", return_value=proc)

        result = enqueue_task_tool(
            tq, mgr, project="p", body="explain the schema",
            allowed_base=str(tmp_path),
        )

        task = tq.get(result["task_id"])
        assert task["mutates_repo"] == 0
        assert task["branch_name"] is None
        assert not result.get("planned_branch")

        mocker.patch("src.branch_prep.is_git_repo", return_value=True)
        is_clean = mocker.patch("src.branch_prep.is_clean")
        checkout_new = mocker.patch("src.branch_prep.checkout_new_branch")
        prepare_ok = branch_prep.prepare_branch(
            tq, task, str((tmp_path / "p").resolve()),
        )

        assert prepare_ok is True
        is_clean.assert_not_called()
        checkout_new.assert_not_called()
        assert tq.get(result["task_id"])["branch_name"] is None
