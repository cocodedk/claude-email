"""Tests for src/task_control.py — cancel + status helpers."""
import os
import signal
import pytest
from src.chat_db import ChatDB
from src.task_queue import TaskQueue
from src.task_control import cancel_running_task, queue_status


@pytest.fixture
def tq(tmp_path):
    path = str(tmp_path / "db")
    ChatDB(path)
    return TaskQueue(path)


class TestToolWrappers:
    def test_cancel_task_tool_rejects_bad_path(self, tq, tmp_path):
        from chat.tools import cancel_task_tool
        result = cancel_task_tool(
            tq, project="never-made", allowed_base=str(tmp_path),
        )
        assert "error" in result

    def test_queue_status_tool_rejects_bad_path(self, tq, tmp_path):
        from chat.tools import queue_status_tool
        result = queue_status_tool(
            tq, project="never-made", allowed_base=str(tmp_path),
        )
        assert "error" in result

    def test_cancel_task_tool_drain_queue_path(self, tq, tmp_path):
        from chat.tools import cancel_task_tool
        (tmp_path / "p").mkdir()
        tq.enqueue(str((tmp_path / "p").resolve()), "pending")
        result = cancel_task_tool(
            tq, project="p", allowed_base=str(tmp_path), drain_queue=True,
        )
        assert result.get("drained") == 1

    def test_queue_status_tool_happy_path(self, tq, tmp_path):
        from chat.tools import queue_status_tool
        (tmp_path / "p").mkdir()
        result = queue_status_tool(
            tq, project="p", allowed_base=str(tmp_path),
        )
        assert result == {"running": None, "pending": []}

    def test_reset_project_tool_rejects_bad_path(self, tq, tmp_path):
        from chat.tools import reset_project_tool
        from src.reset_control import TokenStore
        result = reset_project_tool(
            TokenStore(), project="never", allowed_base=str(tmp_path),
        )
        assert "error" in result

    def test_reset_project_tool_issues_token(self, tq, tmp_path):
        from chat.tools import reset_project_tool
        from src.reset_control import TokenStore
        (tmp_path / "p").mkdir()
        result = reset_project_tool(
            TokenStore(), project="p", allowed_base=str(tmp_path),
        )
        assert result["status"] == "confirm_required"
        assert "confirm_token" in result

    def test_confirm_reset_tool_rejects_bad_path(self, tq, tmp_path):
        from chat.tools import confirm_reset_tool
        from src.reset_control import TokenStore
        result = confirm_reset_tool(
            tq, TokenStore(), project="never", token="x", allowed_base=str(tmp_path),
        )
        assert "error" in result

    def test_confirm_reset_tool_rejects_invalid_token(self, tq, tmp_path):
        from chat.tools import confirm_reset_tool
        from src.reset_control import TokenStore
        (tmp_path / "p").mkdir()
        result = confirm_reset_tool(
            tq, TokenStore(), project="p", token="bogus", allowed_base=str(tmp_path),
        )
        assert "error" in result

    def test_confirm_reset_tool_happy_path(self, tq, tmp_path, mocker):
        from chat.tools import reset_project_tool, confirm_reset_tool
        from src.reset_control import TokenStore
        (tmp_path / "p").mkdir()
        tokens = TokenStore()
        issued = reset_project_tool(
            tokens, project="p", allowed_base=str(tmp_path),
        )
        mocker.patch(
            "src.reset_control.subprocess.run",
            return_value=mocker.MagicMock(returncode=0, stdout="", stderr=""),
        )
        result = confirm_reset_tool(
            tq, tokens, project="p", token=issued["confirm_token"],
            allowed_base=str(tmp_path),
        )
        assert result["status"] == "reset"
