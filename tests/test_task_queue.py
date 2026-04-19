"""Tests for src/task_queue.py — per-project FIFO queue over SQLite."""
import pytest
from src.chat_db import ChatDB
from src.task_queue import TaskQueue


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "tq.db")
    ChatDB(path)
    return path


@pytest.fixture
def tq(db_path):
    return TaskQueue(db_path)


class TestEnqueue:
    def test_enqueue_returns_id_and_row(self, tq):
        tid = tq.enqueue("/proj/a", "do thing")
        row = tq.get(tid)
        assert row["project_path"] == "/proj/a"
        assert row["body"] == "do thing"
        assert row["status"] == "pending"
        assert row["priority"] == 0

    def test_enqueue_priority(self, tq):
        tid = tq.enqueue("/proj/a", "urgent", priority=10)
        assert tq.get(tid)["priority"] == 10


class TestClaimNext:
    def test_claim_next_returns_oldest_pending(self, tq):
        a = tq.enqueue("/p", "a")
        b = tq.enqueue("/p", "b")
        claimed = tq.claim_next("/p")
        assert claimed["id"] == a
        assert claimed["status"] == "running"
        assert tq.get(b)["status"] == "pending"

    def test_claim_next_respects_priority(self, tq):
        low = tq.enqueue("/p", "low", priority=0)
        high = tq.enqueue("/p", "high", priority=10)
        claimed = tq.claim_next("/p")
        assert claimed["id"] == high
        assert tq.get(low)["status"] == "pending"

    def test_claim_next_scoped_to_project(self, tq):
        tq.enqueue("/p1", "a")
        b = tq.enqueue("/p2", "b")
        claimed = tq.claim_next("/p2")
        assert claimed["id"] == b

    def test_claim_next_returns_none_when_empty(self, tq):
        assert tq.claim_next("/nowhere") is None

    def test_claim_next_ignores_non_pending(self, tq):
        tid = tq.enqueue("/p", "a")
        tq.claim_next("/p")  # moves to running
        assert tq.claim_next("/p") is None


class TestLifecycle:
    def test_mark_done_closes_the_task(self, tq):
        tid = tq.enqueue("/p", "a")
        tq.claim_next("/p")
        tq.mark_done(tid)
        row = tq.get(tid)
        assert row["status"] == "done"
        assert row["completed_at"] is not None

    def test_mark_failed_records_error(self, tq):
        tid = tq.enqueue("/p", "a")
        tq.claim_next("/p")
        tq.mark_failed(tid, "boom")
        row = tq.get(tid)
        assert row["status"] == "failed"
        assert row["error_text"] == "boom"

    def test_cancel_marks_cancelled(self, tq):
        tid = tq.enqueue("/p", "a")
        tq.cancel(tid)
        assert tq.get(tid)["status"] == "cancelled"

    def test_set_pid_stores_pid(self, tq):
        tid = tq.enqueue("/p", "a")
        tq.claim_next("/p")
        tq.set_pid(tid, 4242)
        assert tq.get(tid)["pid"] == 4242


class TestListPendingAndRunning:
    def test_list_pending_returns_pending_only(self, tq):
        a = tq.enqueue("/p", "a")
        b = tq.enqueue("/p", "b")
        tq.claim_next("/p")  # a is running
        pending = tq.list_pending("/p")
        assert [p["id"] for p in pending] == [b]

    def test_get_running_returns_running_row(self, tq):
        a = tq.enqueue("/p", "a")
        tq.claim_next("/p")
        running = tq.get_running("/p")
        assert running["id"] == a

    def test_get_running_none_when_idle(self, tq):
        assert tq.get_running("/p") is None


class TestDrainPending:
    def test_drain_pending_cancels_all_pending(self, tq):
        a = tq.enqueue("/p", "a")
        b = tq.enqueue("/p", "b")
        tq.claim_next("/p")  # a is running
        cancelled = tq.drain_pending("/p")
        assert cancelled == 1
        assert tq.get(b)["status"] == "cancelled"
        assert tq.get(a)["status"] == "running"  # running untouched

    def test_drain_pending_scoped(self, tq):
        tq.enqueue("/p1", "a")
        tq.enqueue("/p2", "b")
        assert tq.drain_pending("/p1") == 1
        assert tq.list_pending("/p2")


class TestGetMissing:
    def test_get_returns_none_for_missing(self, tq):
        assert tq.get(9999) is None


class TestListProjectPaths:
    def test_returns_distinct_paths(self, tq):
        tq.enqueue("/a", "x")
        tq.enqueue("/a", "y")
        tq.enqueue("/b", "z")
        assert tq.list_project_paths() == ["/a", "/b"]

    def test_empty(self, tq):
        assert tq.list_project_paths() == []


class TestLatestTask:
    def test_returns_latest(self, tq):
        tq.enqueue("/a", "one")
        last = tq.enqueue("/a", "two")
        assert tq.latest_task("/a")["id"] == last

    def test_none_when_no_tasks(self, tq):
        assert tq.latest_task("/never") is None
