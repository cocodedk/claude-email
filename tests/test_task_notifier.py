"""Tests for src/task_notifier.py — guaranteed task-completion notification."""
import pytest
from src.chat_db import ChatDB
from src.task_notifier import notify_task_done


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "db")
    ChatDB(path)
    return path


def _pending(db_path):
    db = ChatDB(db_path)
    return db.get_pending_messages_for("user")


class TestNotifyTaskDone:
    def test_done_task_queues_branch_hint(self, db_path):
        notify_task_done(db_path, {
            "id": 42, "status": "done",
            "project_path": "/home/u/test-01",
            "branch_name": "claude/task-42-add-tests",
        })
        msgs = _pending(db_path)
        assert len(msgs) == 1
        assert msgs[0]["from_name"] == "agent-test-01"
        assert "Task #42 done" in msgs[0]["body"]
        assert "claude/task-42-add-tests" in msgs[0]["body"]
        assert msgs[0]["type"] == "notify"

    def test_failed_task_includes_error_text(self, db_path):
        notify_task_done(db_path, {
            "id": 5, "status": "failed",
            "project_path": "/home/u/api",
            "branch_name": None,
            "error_text": "claude exited rc=1",
        })
        msgs = _pending(db_path)
        assert "failed" in msgs[0]["body"]
        assert "claude exited rc=1" in msgs[0]["body"]

    def test_cancelled_task(self, db_path):
        notify_task_done(db_path, {
            "id": 9, "status": "cancelled",
            "project_path": "/home/u/web",
            "branch_name": "claude/task-9-wat",
        })
        msgs = _pending(db_path)
        assert "cancelled" in msgs[0]["body"]

    def test_non_git_done_is_still_notified(self, db_path):
        notify_task_done(db_path, {
            "id": 1, "status": "done",
            "project_path": "/home/u/notes",
            "branch_name": None,
        })
        msgs = _pending(db_path)
        assert "non-git" in msgs[0]["body"]

    def test_empty_row_noop(self, db_path):
        notify_task_done(db_path, {})
        assert _pending(db_path) == []

    def test_db_error_is_swallowed(self, tmp_path, mocker):
        # Point at a path where ChatDB creation will fail (e.g. parent missing).
        # Should log warning but not raise.
        mocker.patch(
            "src.task_notifier.ChatDB", side_effect=OSError("db denied"),
        )
        notify_task_done(str(tmp_path / "x"), {
            "id": 1, "status": "done",
            "project_path": "/a/b", "branch_name": None,
        })  # must not raise

    def test_missing_project_path_fallback(self, db_path):
        notify_task_done(db_path, {"id": 1, "status": "done"})
        msgs = _pending(db_path)
        assert msgs[0]["from_name"] == "agent-unknown"
