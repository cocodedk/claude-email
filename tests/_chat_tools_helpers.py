"""Shared fixtures and helpers for chat tools tests.

Underscore-prefixed so pytest skips collection here — the fixture and
helper are imported by `tests/test_chat_tools_*.py` modules.
"""
import pytest
from src.chat_db import ChatDB
from src.task_queue import TaskQueue


@pytest.fixture
def db(tmp_path):
    return ChatDB(str(tmp_path / "test.db"))


def _running_json_task(db, project="/p", body="work"):
    """Create a task in the running state with a JSON-origin marker.
    Used by the new status-envelope tests so they hit the JSON branch
    without hand-crafting INSERT INTO tasks."""
    tq = TaskQueue(db.path)
    task_id = tq.enqueue(project, body, origin_content_type="application/json")
    tq.claim_next(project)
    return task_id
