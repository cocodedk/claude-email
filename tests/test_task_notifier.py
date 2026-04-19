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

    def test_done_with_output_includes_tail(self, db_path):
        notify_task_done(db_path, {
            "id": 7, "status": "done",
            "project_path": "/home/u/test-01",
            "branch_name": "claude/task-7-x",
            "output_text": "wrote 3 files\nall tests pass",
        })
        body = _pending(db_path)[0]["body"]
        assert "wrote 3 files" in body
        assert "tail of output" in body

    def test_failed_with_output_appends_tail(self, db_path):
        notify_task_done(db_path, {
            "id": 8, "status": "failed",
            "project_path": "/home/u/api",
            "branch_name": None,
            "error_text": "claude exited rc=1",
            "output_text": "Traceback line\nAssertionError: boom",
        })
        body = _pending(db_path)[0]["body"]
        assert "AssertionError: boom" in body

    def test_json_origin_emits_result_envelope(self, db_path):
        import json
        notify_task_done(db_path, {
            "id": 11, "status": "done",
            "project_path": "/home/u/test-01",
            "branch_name": "claude/task-11-x",
            "output_text": "wrote 3 files",
            "origin_content_type": "application/json",
        })
        msgs = _pending(db_path)
        assert msgs[0]["content_type"] == "application/json"
        assert msgs[0]["task_id"] == 11
        parsed = json.loads(msgs[0]["body"])
        assert parsed["kind"] == "result"
        assert parsed["task_id"] == 11
        assert parsed["data"]["status"] == "done"
        assert parsed["data"]["branch"] == "claude/task-11-x"
        assert "wrote 3 files" in parsed["data"]["output_tail"]

    def test_json_origin_failed_includes_error(self, db_path):
        import json
        notify_task_done(db_path, {
            "id": 12, "status": "failed",
            "project_path": "/home/u/p",
            "branch_name": None,
            "error_text": "rc=1",
            "output_text": "trace",
            "origin_content_type": "application/json",
        })
        parsed = json.loads(_pending(db_path)[0]["body"])
        assert parsed["data"]["error"] == "rc=1"
        assert parsed["data"]["status"] == "failed"

    def test_plain_origin_still_human_readable(self, db_path):
        notify_task_done(db_path, {
            "id": 13, "status": "done",
            "project_path": "/home/u/p",
            "branch_name": "claude/task-13-x",
            "origin_content_type": "",  # plain text
        })
        msgs = _pending(db_path)
        assert msgs[0]["content_type"] is None  # stays text/plain default
        assert "Task #13 done" in msgs[0]["body"]

    def test_long_output_trimmed_to_600(self, db_path):
        notify_task_done(db_path, {
            "id": 9, "status": "done",
            "project_path": "/home/u/p",
            "branch_name": None,
            "output_text": "x" * 2000,
        })
        body = _pending(db_path)[0]["body"]
        assert len(body) < 1200  # tail is capped well below original
