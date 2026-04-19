"""Tests for src/task_log.py."""
import json
import pytest
from src.task_log import log_task_finished


@pytest.fixture
def row():
    return {
        "id": 42,
        "status": "done",
        "body": "implement tests",
        "branch_name": "claude/task-42-implement-tests",
        "created_at": "2026-04-19T10:00:00",
        "started_at": "2026-04-19T10:01:00",
        "completed_at": "2026-04-19T10:05:00",
        "error_text": None,
    }


def _read(path):
    return path.read_text() if path.exists() else ""


class TestLogTaskFinished:
    def test_creates_jsonl_and_markdown(self, tmp_path, row):
        log_task_finished(str(tmp_path), row)
        claude_dir = tmp_path / ".claude"
        assert (claude_dir / "tasks.jsonl").exists()
        assert (claude_dir / "CHANGELOG-claude.md").exists()

    def test_jsonl_has_valid_json_per_line(self, tmp_path, row):
        log_task_finished(str(tmp_path), row)
        log_task_finished(str(tmp_path), {**row, "id": 43})
        content = (tmp_path / ".claude" / "tasks.jsonl").read_text()
        lines = [ln for ln in content.splitlines() if ln.strip()]
        assert len(lines) == 2
        parsed = [json.loads(ln) for ln in lines]
        assert parsed[0]["id"] == 42
        assert parsed[1]["id"] == 43

    def test_markdown_contains_request_body_and_branch(self, tmp_path, row):
        log_task_finished(str(tmp_path), row)
        md = (tmp_path / ".claude" / "CHANGELOG-claude.md").read_text()
        assert "Task #42" in md
        assert "implement tests" in md
        assert "claude/task-42-implement-tests" in md
        assert "done" in md

    def test_failed_status_shown_with_error(self, tmp_path, row):
        failed = {**row, "status": "failed", "error_text": "boom", "branch_name": None}
        log_task_finished(str(tmp_path), failed)
        md = (tmp_path / ".claude" / "CHANGELOG-claude.md").read_text()
        assert "failed" in md
        assert "boom" in md

    def test_cancelled_status_shown(self, tmp_path, row):
        cancelled = {**row, "status": "cancelled"}
        log_task_finished(str(tmp_path), cancelled)
        md = (tmp_path / ".claude" / "CHANGELOG-claude.md").read_text()
        assert "cancelled" in md

    def test_missing_project_dir_is_tolerated(self, tmp_path, row, mocker):
        """mkdir failure should log-warn, not raise."""
        mocker.patch.object(type(tmp_path), "__truediv__", side_effect=OSError("denied"))
        log_task_finished(str(tmp_path), row)  # just must not raise

    def test_body_is_truncated_to_500_chars(self, tmp_path, row):
        long_body = "x" * 1200
        log_task_finished(str(tmp_path), {**row, "body": long_body})
        md = (tmp_path / ".claude" / "CHANGELOG-claude.md").read_text()
        assert "x" * 500 in md
        # not 1200 x's
        assert "x" * 501 not in md

    def test_jsonl_write_error_logged_not_raised(self, tmp_path, row, mocker):
        from src import task_log
        real_open = task_log.Path.open
        count = {"i": 0}

        def flaky_open(self, *a, **kw):
            count["i"] += 1
            # First call is jsonl — raise. Second call is markdown — allow.
            if count["i"] == 1:
                raise OSError("jsonl denied")
            return real_open(self, *a, **kw)

        mocker.patch.object(task_log.Path, "open", flaky_open)
        log_task_finished(str(tmp_path), row)  # must not raise
        # markdown still written
        assert (tmp_path / ".claude" / "CHANGELOG-claude.md").exists()

    def test_markdown_write_error_logged_not_raised(self, tmp_path, row, mocker):
        from src import task_log
        real_open = task_log.Path.open

        def flaky_open(self, *a, **kw):
            if self.name == "CHANGELOG-claude.md":
                raise OSError("md denied")
            return real_open(self, *a, **kw)

        mocker.patch.object(task_log.Path, "open", flaky_open)
        log_task_finished(str(tmp_path), row)  # must not raise
