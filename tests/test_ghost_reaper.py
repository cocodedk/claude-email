"""Tests for src/ghost_reaper.py."""
import pytest
from src.chat_db import ChatDB
from src.ghost_reaper import sweep_ghosts
from src.task_queue import TaskQueue


@pytest.fixture
def tq(tmp_path):
    path = str(tmp_path / "db")
    ChatDB(path)
    return TaskQueue(path)


class TestSweepGhosts:
    def test_dead_pid_marked_failed(self, tq, mocker):
        tid = tq.enqueue("/p", "x")
        tq.claim_next("/p")
        tq.set_pid(tid, 9999)
        mocker.patch("src.ghost_reaper.is_alive", return_value=False)
        assert sweep_ghosts(tq) == 1
        row = tq.get(tid)
        assert row["status"] == "failed"
        assert "exited unexpectedly" in row["error_text"]

    def test_alive_pid_left_alone(self, tq, mocker):
        tid = tq.enqueue("/p", "x")
        tq.claim_next("/p")
        tq.set_pid(tid, 12345)
        mocker.patch("src.ghost_reaper.is_alive", return_value=True)
        assert sweep_ghosts(tq) == 0
        assert tq.get(tid)["status"] == "running"

    def test_pid_zero_or_none_skipped(self, tq, mocker):
        """A claimed task that hasn't yet called set_pid has pid=None/0
        — leave it alone, the worker may still be preparing the branch."""
        tid = tq.enqueue("/p", "x")
        tq.claim_next("/p")
        # No set_pid → pid is NULL
        mocker.patch("src.ghost_reaper.is_alive", return_value=False)
        assert sweep_ghosts(tq) == 0
        assert tq.get(tid)["status"] == "running"

    def test_multiple_dead_tasks_reaped(self, tq, mocker):
        a = tq.enqueue("/pa", "a")
        tq.claim_next("/pa")
        tq.set_pid(a, 100)
        b = tq.enqueue("/pb", "b")
        tq.claim_next("/pb")
        tq.set_pid(b, 200)
        mocker.patch("src.ghost_reaper.is_alive", return_value=False)
        assert sweep_ghosts(tq) == 2
        assert tq.get(a)["status"] == "failed"
        assert tq.get(b)["status"] == "failed"

    def test_notification_queued_on_reap(self, tq, mocker, tmp_path):
        tid = tq.enqueue(str(tmp_path), "reap me")
        tq.claim_next(str(tmp_path))
        tq.set_pid(tid, 9999)
        mocker.patch("src.ghost_reaper.is_alive", return_value=False)
        sweep_ghosts(tq)
        # Reaped tasks queue an agent→user notification
        msgs = ChatDB(tq.path).get_pending_messages_for("user")
        assert any("Task #" in m["body"] for m in msgs)
