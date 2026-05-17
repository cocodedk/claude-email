"""claim_next must enforce one running task per project so branch
reuse can never end up with two concurrent workers on the same branch.

Round-3 reviewer blocker 1: today's claim_next does NOT enforce this
— two consecutive claim_next calls without an intervening mark_done
will claim two rows. This file pins the new invariant added by the
NOT EXISTS guard in src/task_queue.py."""
from src.chat_db import ChatDB
from src.task_queue import TaskQueue


def test_claim_next_yields_one_at_a_time(tmp_path):
    path = str(tmp_path / "db")
    ChatDB(path)
    tq = TaskQueue(path)
    a = tq.enqueue("/p", "task a", branch_name="claude/task-1-foo")
    b = tq.enqueue("/p", "task b", branch_name="claude/task-1-foo")

    first = tq.claim_next("/p")
    assert first["id"] == a
    assert tq.claim_next("/p") is None
    tq.mark_done(first["id"])
    second = tq.claim_next("/p")
    assert second["id"] == b


def test_second_claim_returns_none_even_with_priority(tmp_path):
    """A higher-priority pending task does NOT preempt a running one —
    the NOT EXISTS guard fires before priority ordering."""
    path = str(tmp_path / "db")
    ChatDB(path)
    tq = TaskQueue(path)
    tq.enqueue("/p", "low", priority=0)
    tq.claim_next("/p")
    tq.enqueue("/p", "urgent", priority=10)
    assert tq.claim_next("/p") is None


def test_running_task_is_singleton_per_project(tmp_path):
    path = str(tmp_path / "db")
    ChatDB(path)
    tq = TaskQueue(path)
    tq.enqueue("/p", "a")
    tq.enqueue("/p", "b")
    tq.enqueue("/p", "c")
    tq.claim_next("/p")
    running = tq._conn.execute(
        "SELECT * FROM tasks WHERE project_path='/p' AND status='running'"
    ).fetchall()
    assert len(running) == 1


def test_two_projects_can_run_concurrently(tmp_path):
    """Per-project workers can run in parallel — only intra-project
    serialization is the invariant. The NOT EXISTS guard is scoped to
    project_path so a /p1 claim does NOT block a /p2 claim."""
    path = str(tmp_path / "db")
    ChatDB(path)
    tq = TaskQueue(path)
    tq.enqueue("/p1", "x")
    tq.enqueue("/p2", "y")
    assert tq.claim_next("/p1") is not None
    assert tq.claim_next("/p2") is not None


def test_failed_task_does_not_block_next_claim(tmp_path):
    """A 'failed' row is not 'running', so it doesn't trip the NOT
    EXISTS guard."""
    path = str(tmp_path / "db")
    ChatDB(path)
    tq = TaskQueue(path)
    a = tq.enqueue("/p", "a")
    b = tq.enqueue("/p", "b")
    tq.claim_next("/p")
    tq.mark_failed(a, "boom")
    second = tq.claim_next("/p")
    assert second is not None
    assert second["id"] == b
