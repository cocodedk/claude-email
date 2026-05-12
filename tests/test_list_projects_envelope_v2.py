"""Wire shape contract for list_projects across envelope versions."""
import pytest

from chat.project_tools import list_projects_tool
from src.chat_db import ChatDB
from src.task_queue import TaskQueue


@pytest.fixture
def setup(tmp_path, monkeypatch):
    base = tmp_path / "projects"
    base.mkdir()
    (base / "proj-a").mkdir()
    (base / "proj-a" / ".git").mkdir()
    db_path = str(tmp_path / "chat.db")
    chat_db = ChatDB(db_path)
    queue = TaskQueue(db_path)
    monkeypatch.delenv("TASK_STATE_FADE_SEC", raising=False)
    return base, queue, chat_db


def test_v1_returns_legacy_agent_status_vocab(setup):
    base, queue, chat_db = setup
    out = list_projects_tool(
        queue, allowed_base=str(base), chat_db=chat_db,
        envelope_version=1,
    )
    row = out["projects"][0]
    assert row["agent_status"] in ("connected", "disconnected", "absent")
    assert "task_state" not in row


def test_v2_returns_new_vocab_and_task_state_for_pending(setup):
    base, queue, chat_db = setup
    queue.enqueue(str(base / "proj-a"), "do x")
    out = list_projects_tool(
        queue, allowed_base=str(base), chat_db=chat_db,
        envelope_version=2,
    )
    row = out["projects"][0]
    assert row["agent_status"] in ("online", "stale", "offline")
    assert row["task_state"] == "waiting"


def test_v2_no_agent_no_task_is_offline_and_null(setup):
    base, queue, chat_db = setup
    out = list_projects_tool(
        queue, allowed_base=str(base), chat_db=chat_db,
        envelope_version=2,
    )
    row = out["projects"][0]
    assert row["agent_status"] == "offline"
    assert row["task_state"] is None


def test_default_envelope_version_is_1(setup):
    """Callers that don't pass the new param keep legacy shape."""
    base, queue, chat_db = setup
    out = list_projects_tool(
        queue, allowed_base=str(base), chat_db=chat_db,
    )
    row = out["projects"][0]
    assert "task_state" not in row
    assert row["agent_status"] in ("connected", "disconnected", "absent")


def test_v2_without_chat_db_uses_offline(setup):
    base, queue, _ = setup
    out = list_projects_tool(
        queue, allowed_base=str(base), envelope_version=2,
    )
    row = out["projects"][0]
    assert row["agent_status"] == "offline"
    assert row["task_state"] is None
