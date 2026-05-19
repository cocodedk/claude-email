"""Tests for the shared SQLite database layer (ChatDB) — ownership probe + tx."""
import pytest
from src.chat_db import ChatDB


@pytest.fixture
def db(tmp_path):
    return ChatDB(str(tmp_path / "test.db"))


class TestFindLiveOwner:
    """Ownership probe used by scripts/chat-register-self.py."""

    def test_no_row_returns_none(self, db):
        assert db.find_live_owner("agent-ghost", "/nowhere") is None

    def test_dead_owner_returns_none(self, db):
        db.register_agent("agent-a", "/p", pid=99999999)  # not alive
        assert db.find_live_owner("agent-a", "/p") is None

    def test_live_name_owner_returned(self, db):
        import os as _os
        db.register_agent("agent-a", "/p", pid=_os.getpid())
        owner = db.find_live_owner("agent-a", "/elsewhere")
        assert owner == {"name": "agent-a", "pid": _os.getpid()}

    def test_live_project_owner_returned_under_different_name(self, db):
        import os as _os
        db.register_agent("agent-a", "/shared", pid=_os.getpid())
        owner = db.find_live_owner("agent-b", "/shared")
        assert owner == {"name": "agent-a", "pid": _os.getpid()}

    def test_exclude_pid_filters_name_match(self, db):
        """exclude_pid lets our own session exempt itself from the probe."""
        import os as _os
        db.register_agent("agent-a", "/p", pid=_os.getpid())
        assert db.find_live_owner(
            "agent-a", "/p", exclude_pid=_os.getpid(),
        ) is None

    def test_exclude_pid_filters_project_match(self, db):
        import os as _os
        db.register_agent("agent-a", "/shared", pid=_os.getpid())
        assert db.find_live_owner(
            "agent-b", "/shared", exclude_pid=_os.getpid(),
        ) is None


class TestRegisterAgentTransaction:
    """register_agent runs inside _run_tx (IMMEDIATE transaction):
    AgentNameTaken rolls back the entire tx and leaves the DB clean."""

    def test_agent_name_taken_rolls_back_atomically(self, tmp_path):
        """AgentNameTaken raised from _impl_register_agent causes _run_tx
        to roll back — the conflicting INSERT never commits."""
        import os as _os
        from src.chat_errors import AgentNameTaken as _ANT

        db = ChatDB(str(tmp_path / "tx.db"))
        # Owner registered first.
        db.register_agent("agent-x", "/tmp", pid=_os.getpid())
        events_before = db._conn.execute(
            "SELECT COUNT(*) FROM events"
        ).fetchone()[0]

        # Attempt to steal the slot with a different (non-existent) pid.
        with pytest.raises(_ANT):
            db.register_agent("agent-x", "/tmp2", pid=_os.getpid() + 10_000_000)

        # DB is clean: event count unchanged (rollback held).
        events_after = db._conn.execute(
            "SELECT COUNT(*) FROM events"
        ).fetchone()[0]
        assert events_after == events_before, (
            "partial events committed despite AgentNameTaken rollback"
        )
        # Original row untouched.
        agent = db.get_agent("agent-x")
        assert agent["project_path"] == "/tmp"
        assert agent["pid"] == _os.getpid()
