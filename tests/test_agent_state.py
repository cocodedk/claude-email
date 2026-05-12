"""3-state liveness vocabulary for envelope v: 2 list_projects."""
import os
from datetime import datetime, timedelta, timezone

import pytest

from src.agent_registry import DEFAULT_AGENT_FRESHNESS_SEC
from src.chat_db import ChatDB
from src.dashboard_queries import DEFAULT_AGENT_STALE_SECS


def _ts(seconds_ago: int) -> str:
    return (
        datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    ).isoformat()


@pytest.fixture
def db(tmp_path):
    return ChatDB(str(tmp_path / "t.db"))


def _row(db, name, path, *, pid, status, seen):
    db._conn.execute(
        "INSERT INTO agents (name, project_path, pid, status, "
        "last_seen_at, registered_at) VALUES (?, ?, ?, ?, ?, ?)",
        (name, path, pid, status, seen, seen),
    )
    db._conn.commit()


def test_no_row_returns_offline(db):
    assert db.agent_state_for_project("/x") == "offline"


def test_pid_alive_forces_online_even_when_seen_old(db):
    _row(db, "a", "/x", pid=os.getpid(), status="running",
         seen=_ts(DEFAULT_AGENT_STALE_SECS + 60))
    assert db.agent_state_for_project("/x") == "online"


def test_pid_dead_forces_offline(db):
    _row(db, "a", "/x", pid=999999, status="running", seen=_ts(10))
    assert db.agent_state_for_project("/x") == "offline"


def test_no_pid_recent_seen_is_online(db):
    _row(db, "a", "/x", pid=None, status="running", seen=_ts(60))
    assert db.agent_state_for_project("/x") == "online"


def test_no_pid_mid_window_is_stale(db):
    _row(db, "a", "/x", pid=None, status="running",
         seen=_ts(DEFAULT_AGENT_FRESHNESS_SEC + 60))
    assert db.agent_state_for_project("/x") == "stale"


def test_no_pid_beyond_ghost_is_offline(db):
    _row(db, "a", "/x", pid=None, status="running",
         seen=_ts(DEFAULT_AGENT_STALE_SECS + 60))
    assert db.agent_state_for_project("/x") == "offline"


def test_deregistered_status_is_offline(db):
    _row(db, "a", "/x", pid=None, status="deregistered", seen=_ts(10))
    assert db.agent_state_for_project("/x") == "offline"


def test_multiple_rows_best_wins(db):
    _row(db, "a", "/x", pid=None, status="running",
         seen=_ts(DEFAULT_AGENT_STALE_SECS + 60))
    _row(db, "b", "/x", pid=None, status="running", seen=_ts(30))
    assert db.agent_state_for_project("/x") == "online"


def test_stale_beats_offline_when_no_online_present(db):
    _row(db, "a", "/x", pid=None, status="running",
         seen=_ts(DEFAULT_AGENT_STALE_SECS + 60))  # offline
    _row(db, "b", "/x", pid=None, status="running",
         seen=_ts(DEFAULT_AGENT_FRESHNESS_SEC + 60))  # stale
    assert db.agent_state_for_project("/x") == "stale"
