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
    def test_commit_project_tool_happy_path(self, tmp_path, mocker):
        from chat.tools import commit_project_tool
        (tmp_path / "p").mkdir()
        mocker.patch(
            "chat.project_mutations.commit_all", return_value=(True, "a1b2c3d"),
        )
        result = commit_project_tool(
            project="p", message="WIP", allowed_base=str(tmp_path),
        )
        assert result["status"] == "committed"
        assert result["sha"] == "a1b2c3d"

    def test_commit_project_tool_rejects_bad_path(self, tmp_path):
        from chat.tools import commit_project_tool
        result = commit_project_tool(
            project="never-made", message="x", allowed_base=str(tmp_path),
        )
        assert "error" in result

    def test_commit_project_tool_surfaces_git_error(self, tmp_path, mocker):
        from chat.tools import commit_project_tool
        (tmp_path / "p").mkdir()
        mocker.patch(
            "chat.project_mutations.commit_all",
            return_value=(False, "nothing to commit"),
        )
        result = commit_project_tool(
            project="p", message="x", allowed_base=str(tmp_path),
        )
        assert result["error"] == "nothing to commit"

    def test_commit_project_tool_with_push_runs_push(self, tmp_path, mocker):
        """'commit and push the dirty repo' must be a single tool call so
        the router doesn't fall through to chat_enqueue_task."""
        from chat.tools import commit_project_tool
        (tmp_path / "p").mkdir()
        mocker.patch(
            "chat.project_mutations.commit_all", return_value=(True, "deadbeef"),
        )
        push = mocker.patch(
            "chat.project_mutations.push_current_branch",
            return_value=(True, "pushed"),
        )
        result = commit_project_tool(
            project="p", message="WIP", push=True, allowed_base=str(tmp_path),
        )
        assert result == {
            "status": "committed", "sha": "deadbeef",
            "project": str((tmp_path / "p").resolve()),
            "pushed": True, "push_error": None,
        }
        push.assert_called_once()

    def test_commit_project_tool_push_failure_after_commit(self, tmp_path, mocker):
        """Commit succeeded but push failed — push_error carries the reason
        and pushed stays False."""
        from chat.tools import commit_project_tool
        (tmp_path / "p").mkdir()
        mocker.patch(
            "chat.project_mutations.commit_all", return_value=(True, "abc1234"),
        )
        mocker.patch(
            "chat.project_mutations.push_current_branch",
            return_value=(False, "no upstream"),
        )
        result = commit_project_tool(
            project="p", message="x", push=True, allowed_base=str(tmp_path),
        )
        assert result["status"] == "committed"
        assert result["sha"] == "abc1234"
        assert result["pushed"] is False
        assert "no upstream" in result["push_error"]

    def test_commit_project_tool_push_default_off(self, tmp_path, mocker):
        from chat.tools import commit_project_tool
        (tmp_path / "p").mkdir()
        mocker.patch(
            "chat.project_mutations.commit_all", return_value=(True, "abc"),
        )
        push = mocker.patch("chat.project_mutations.push_current_branch")
        result = commit_project_tool(
            project="p", message="x", allowed_base=str(tmp_path),
        )
        assert result["status"] == "committed"
        assert result["pushed"] is False
        assert result["push_error"] is None
        push.assert_not_called()

    def test_where_am_i_tool_empty(self, tq):
        from chat.tools import where_am_i_tool

        class _Mgr:
            def pid_of(self, _):
                return None
        result = where_am_i_tool(tq, _Mgr())
        assert result == {"projects": []}

    def test_where_am_i_tool_with_activity(self, tq, tmp_path):
        from chat.tools import where_am_i_tool

        class _Mgr:
            def pid_of(self, path):
                return 4242 if path.endswith("alpha") else None

        (tmp_path / "alpha").mkdir()
        (tmp_path / "beta").mkdir()
        alpha = str((tmp_path / "alpha").resolve())
        beta = str((tmp_path / "beta").resolve())
        tq.enqueue(alpha, "build")
        tq.claim_next(alpha)
        tq.enqueue(alpha, "pending too")
        tq.enqueue(beta, "done task")
        tq.claim_next(beta)
        tq.mark_done(tq.get_running(beta)["id"])

        result = where_am_i_tool(tq, _Mgr())
        by_name = {p["project_name"]: p for p in result["projects"]}
        assert by_name["alpha"]["running_task"] is not None
        assert by_name["alpha"]["pending_count"] == 1
        assert by_name["alpha"]["worker_pid"] == 4242
        assert by_name["beta"]["pending_count"] == 0
        assert by_name["beta"]["last_task_status"] == "done"
