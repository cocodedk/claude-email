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


class TestCancelRunningTask:
    def test_cancels_running_and_signals_pid(self, tq, mocker):
        tid = tq.enqueue("/p", "x")
        tq.claim_next("/p")
        tq.set_pid(tid, 12345)
        killed = mocker.patch("src.task_control.os.kill")
        mocker.patch("src.task_control._wait_for_exit", return_value=True)
        result = cancel_running_task(tq, "/p")
        assert result["status"] == "cancelled"
        assert result["task_id"] == tid
        killed.assert_any_call(12345, signal.SIGTERM)
        assert tq.get(tid)["status"] == "cancelled"

    def test_no_running_task_reports_idle(self, tq):
        result = cancel_running_task(tq, "/p")
        assert result == {"status": "idle"}

    def test_sigkill_on_timeout(self, tq, mocker):
        tid = tq.enqueue("/p", "x")
        tq.claim_next("/p")
        tq.set_pid(tid, 99)
        killed = mocker.patch("src.task_control.os.kill")
        mocker.patch("src.task_control._wait_for_exit", return_value=False)
        cancel_running_task(tq, "/p", grace_seconds=0.0)
        calls = [c for c in killed.call_args_list]
        sigs = [c.args[1] for c in calls]
        assert signal.SIGTERM in sigs
        assert signal.SIGKILL in sigs

    def test_drain_queue_also_cancels_pending(self, tq, mocker):
        tid = tq.enqueue("/p", "running")
        pending = tq.enqueue("/p", "pending")
        tq.claim_next("/p")
        tq.set_pid(tid, 1)
        mocker.patch("src.task_control.os.kill")
        mocker.patch("src.task_control._wait_for_exit", return_value=True)
        result = cancel_running_task(tq, "/p", drain_queue=True)
        assert result["drained"] == 1
        assert tq.get(pending)["status"] == "cancelled"

    def test_missing_pid_still_marks_cancelled(self, tq, mocker):
        tid = tq.enqueue("/p", "x")
        tq.claim_next("/p")
        # no set_pid — claimed but PID not yet recorded
        result = cancel_running_task(tq, "/p")
        assert result["status"] == "cancelled"
        assert tq.get(tid)["status"] == "cancelled"

    def test_kill_esrch_is_tolerated(self, tq, mocker):
        tid = tq.enqueue("/p", "x")
        tq.claim_next("/p")
        tq.set_pid(tid, 12345)
        mocker.patch(
            "src.task_control.os.kill", side_effect=ProcessLookupError(),
        )
        result = cancel_running_task(tq, "/p")
        assert result["status"] == "cancelled"


class TestWaitForExit:
    def test_returns_true_for_nonpositive_pid(self):
        from src.task_control import _wait_for_exit
        assert _wait_for_exit(0, 1.0) is True
        assert _wait_for_exit(-1, 1.0) is True

    def test_polls_until_dead(self, mocker):
        from src.task_control import _wait_for_exit
        mocker.patch("src.task_control.is_alive", side_effect=[True, False])
        mocker.patch("src.task_control.time.sleep")
        assert _wait_for_exit(1234, grace_seconds=5.0) is True

    def test_returns_false_on_deadline(self, mocker):
        from src.task_control import _wait_for_exit
        mocker.patch("src.task_control.is_alive", return_value=True)
        mocker.patch("src.task_control.time.sleep")
        # grace_seconds=0.0 → loop condition fails immediately → final is_alive = True → return False
        assert _wait_for_exit(1234, grace_seconds=0.0) is False


class TestCancelDrainIdle:
    def test_drain_queue_when_idle(self, tq):
        tq.enqueue("/p", "pending-a")
        tq.enqueue("/p", "pending-b")
        result = cancel_running_task(tq, "/p", drain_queue=True)
        assert result == {"status": "idle", "drained": 2}


class TestSigkillEsrch:
    def test_sigkill_process_lookup_error_is_tolerated(self, tq, mocker):
        tid = tq.enqueue("/p", "x")
        tq.claim_next("/p")
        tq.set_pid(tid, 1234)
        calls = {"i": 0}

        def fake_kill(pid, sig):
            calls["i"] += 1
            if calls["i"] == 2:  # SIGKILL step
                raise ProcessLookupError()

        mocker.patch("src.task_control.os.kill", side_effect=fake_kill)
        cancel_running_task(tq, "/p", grace_seconds=0.0, wait_fn=lambda *_: False)
        assert tq.get(tid)["status"] == "cancelled"


class TestQueueStatus:
    def test_empty_queue(self, tq):
        assert queue_status(tq, "/p") == {"running": None, "pending": []}

    def test_with_running_and_pending(self, tq):
        running_id = tq.enqueue("/p", "now")
        tq.claim_next("/p")
        tq.set_pid(running_id, 77)
        pending_a = tq.enqueue("/p", "next1")
        pending_b = tq.enqueue("/p", "next2")
        result = queue_status(tq, "/p")
        assert result["running"]["id"] == running_id
        assert result["running"]["pid"] == 77
        assert [p["id"] for p in result["pending"]] == [pending_a, pending_b]


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
        """UX fix: 'commit and push the dirty repo' must be a single tool
        call that commits AND pushes — otherwise the LLM router falls
        through to chat_enqueue_task and branches the work."""
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
        assert result["status"] == "committed-and-pushed"
        assert result["sha"] == "deadbeef"
        push.assert_called_once()

    def test_commit_project_tool_push_failure_after_commit(self, tmp_path, mocker):
        """Commit succeeded but push failed — surface the push error so the
        user sees the partial outcome."""
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
        assert "no upstream" in result["push_error"]

    def test_commit_project_tool_push_default_off(self, tmp_path, mocker):
        """Default push=False — preserves existing callers' behavior."""
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
