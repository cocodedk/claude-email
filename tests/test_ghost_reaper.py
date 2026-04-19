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

    def test_null_pid_within_grace_left_alone(self, tq, mocker):
        """A claimed task with pid=None and started_at recent is still
        preparing the branch — don't reap prematurely."""
        import datetime as _dt
        tid = tq.enqueue("/p", "x")
        tq.claim_next("/p")
        # started_at just happened; pid still NULL
        mocker.patch("src.ghost_reaper.is_alive", return_value=False)
        assert sweep_ghosts(tq) == 0
        assert tq.get(tid)["status"] == "running"

    def test_null_pid_past_grace_is_reaped(self, tq, mocker):
        """A claimed task with pid=None past the grace window — worker
        died before set_pid (crash, missing binary, etc.)."""
        tid = tq.enqueue("/p", "x")
        tq.claim_next("/p")
        # Rewrite started_at to be old
        import sqlite3
        conn = sqlite3.connect(tq.path)
        conn.execute(
            "UPDATE tasks SET started_at='2020-01-01T00:00:00+00:00' WHERE id=?",
            (tid,),
        )
        conn.commit()
        conn.close()
        n = sweep_ghosts(tq)
        assert n == 1
        row = tq.get(tid)
        assert row["status"] == "failed"
        assert "never set_pid" in row["error_text"]

    def test_null_pid_unparseable_started_at_is_reaped(self, tq, mocker):
        tid = tq.enqueue("/p", "x")
        tq.claim_next("/p")
        import sqlite3
        conn = sqlite3.connect(tq.path)
        conn.execute("UPDATE tasks SET started_at='' WHERE id=?", (tid,))
        conn.commit()
        conn.close()
        assert sweep_ghosts(tq) == 1  # age=inf → past grace → reap

    def test_notification_queued_on_reap(self, tq, mocker, tmp_path):
        tid = tq.enqueue(str(tmp_path), "reap me")
        tq.claim_next(str(tmp_path))
        tq.set_pid(tid, 9999)
        mocker.patch("src.ghost_reaper.is_alive", return_value=False)
        sweep_ghosts(tq)
        # Reaped tasks queue an agent→user notification
        msgs = ChatDB(tq.path).get_pending_messages_for("user")
        assert any("Task #" in m["body"] for m in msgs)
