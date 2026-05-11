"""Task-state vocabulary for envelope v: 2 list_projects."""
import pytest

from src.chat_db import ChatDB
from src.task_queue import TaskQueue
from src.task_state import (
    DEFAULT_TASK_STATE_FADE_SEC,
    task_state_for_project,
)


@pytest.fixture
def queue(tmp_path):
    path = str(tmp_path / "q.db")
    ChatDB(path)  # bootstrap schema (per src/task_queue.py module docstring)
    return TaskQueue(path)


def test_no_tasks_returns_none(queue):
    assert task_state_for_project(queue, "/x") is None


def test_running_task_is_working(queue):
    queue.enqueue("/x", "do thing")
    queue.claim_next("/x")
    assert task_state_for_project(queue, "/x") == "working"


def test_pending_only_is_waiting(queue):
    queue.enqueue("/x", "do thing")
    assert task_state_for_project(queue, "/x") == "waiting"


def test_done_within_fade_is_completed(queue):
    tid = queue.enqueue("/x", "do thing")
    queue.claim_next("/x")
    queue.mark_done(tid)
    assert task_state_for_project(queue, "/x") == "completed"


def test_done_beyond_fade_is_none(queue):
    tid = queue.enqueue("/x", "do thing")
    queue.claim_next("/x")
    queue.mark_done(tid)
    assert task_state_for_project(queue, "/x", fade_secs=0) is None


def test_failed_within_fade_is_error(queue):
    tid = queue.enqueue("/x", "do thing")
    queue.claim_next("/x")
    queue.mark_failed(tid, "boom")
    assert task_state_for_project(queue, "/x") == "error"


def test_cancelled_drained_maps_to_completed(queue):
    queue.enqueue("/x", "do thing")
    queue.drain_pending("/x")
    assert task_state_for_project(queue, "/x") == "completed"


def test_running_beats_pending(queue):
    queue.enqueue("/x", "first")
    queue.enqueue("/x", "second")
    queue.claim_next("/x")
    assert task_state_for_project(queue, "/x") == "working"


def test_default_fade_is_30_seconds():
    assert DEFAULT_TASK_STATE_FADE_SEC == 30


def test_env_var_overrides_default(queue, monkeypatch):
    tid = queue.enqueue("/x", "do thing")
    queue.claim_next("/x")
    queue.mark_done(tid)
    monkeypatch.setenv("TASK_STATE_FADE_SEC", "0")
    assert task_state_for_project(queue, "/x") is None


def test_invalid_env_var_falls_back_to_default(queue, monkeypatch):
    tid = queue.enqueue("/x", "do thing")
    queue.claim_next("/x")
    queue.mark_done(tid)
    monkeypatch.setenv("TASK_STATE_FADE_SEC", "not-an-int")
    # default (30s) still applies → completed visible
    assert task_state_for_project(queue, "/x") == "completed"
