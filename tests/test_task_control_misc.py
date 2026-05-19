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
