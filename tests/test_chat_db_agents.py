"""Tests for the shared SQLite database layer (ChatDB) — agent registration."""
import os
import pytest
from src.chat_db import ChatDB, AgentNameTaken


@pytest.fixture
def db(tmp_path):
    return ChatDB(str(tmp_path / "test.db"))


class TestAgents:
    def test_register_agent_returns_dict(self, db):
        result = db.register_agent("agent-fits", "/projects/fits")
        assert isinstance(result, dict)
        assert result["name"] == "agent-fits"
        assert result["project_path"] == "/projects/fits"
        assert result["status"] == "running"

    def test_register_agent_upsert(self, db):
        db.register_agent("a1", "/old/path")
        db.register_agent("a1", "/new/path")
        agent = db.get_agent("a1")
        assert agent["project_path"] == "/new/path"
        assert agent["status"] == "running"

    def test_get_agent_missing_returns_none(self, db):
        assert db.get_agent("nonexistent") is None

    def test_list_agents_empty(self, db):
        assert db.list_agents() == []

    def test_list_agents_multiple(self, db):
        db.register_agent("a1", "/p1")
        db.register_agent("a2", "/p2")
        agents = db.list_agents()
        assert len(agents) == 2
        names = {a["name"] for a in agents}
        assert names == {"a1", "a2"}

    def test_update_agent_status(self, db):
        db.register_agent("a1", "/p")
        db.update_agent_status("a1", "idle")
        assert db.get_agent("a1")["status"] == "idle"

    def test_update_agent_pid(self, db):
        db.register_agent("a1", "/p")
        db.update_agent_pid("a1", 12345)
        assert db.get_agent("a1")["pid"] == 12345

    def test_touch_agent_updates_last_seen(self, db):
        db.register_agent("a1", "/p")
        first = db.get_agent("a1")["last_seen_at"]
        import time
        time.sleep(0.01)
        db.touch_agent("a1")
        second = db.get_agent("a1")["last_seen_at"]
        assert second >= first

    def test_register_with_pid_stores_pid(self, db):
        db.register_agent("a1", "/p", pid=4242)
        assert db.get_agent("a1")["pid"] == 4242

    def test_register_same_pid_refreshes(self, db):
        db.register_agent("a1", "/p", pid=os.getpid())
        db.register_agent("a1", "/p2", pid=os.getpid())
        agent = db.get_agent("a1")
        assert agent["project_path"] == "/p2"
        assert agent["pid"] == os.getpid()

    def test_register_different_live_pid_raises(self, db):
        db.register_agent("a1", "/p", pid=os.getpid())
        with pytest.raises(AgentNameTaken) as excinfo:
            db.register_agent("a1", "/p", pid=os.getpid() + 10_000_000)
        assert excinfo.value.owner_pid == os.getpid()

    def test_register_different_dead_pid_takes_over(self, db):
        db.register_agent("a1", "/p", pid=99_999_999)
        db.register_agent("a1", "/p2", pid=os.getpid())
        agent = db.get_agent("a1")
        assert agent["pid"] == os.getpid()
        assert agent["project_path"] == "/p2"

    def test_register_legacy_no_pid_still_upserts(self, db):
        db.register_agent("a1", "/old")
        db.register_agent("a1", "/new")
        assert db.get_agent("a1")["project_path"] == "/new"

    def test_register_agent_recovers_failed_messages(self, db):
        """Re-register reclaims escalated mail (closes permanent-loss incident)."""
        m1 = db.insert_message("p", "agent-recover", "lost-1", "notify")
        m2 = db.insert_message("p", "agent-recover", "lost-2", "notify")
        db.mark_message_failed(m1["id"])
        db.mark_message_failed(m2["id"])
        assert db.get_pending_messages_for("agent-recover") == []

        db.register_agent("agent-recover", "/projects/recover")

        pending = db.get_pending_messages_for("agent-recover")
        assert {m["body"] for m in pending} == {"lost-1", "lost-2"}

    def test_register_agent_logs_messages_recovered_event(self, db):
        """Recovery event lets operators correlate with bus-incident windows."""
        m = db.insert_message("p", "agent-log", "lost", "notify")
        db.mark_message_failed(m["id"])

        # First registration — nothing failed yet for a fresh name; no
        # recovery event.
        db.register_agent("agent-other", "/p")
        events = db._conn.execute(
            "SELECT event_type FROM events WHERE participant=?",
            ("agent-other",),
        ).fetchall()
        assert all(e["event_type"] != "messages_recovered" for e in events)

        # Re-register agent-log with one failed message → event fires.
        db.register_agent("agent-log", "/projects/log")
        events = db._conn.execute(
            "SELECT event_type, summary FROM events "
            "WHERE participant=? AND event_type='messages_recovered'",
            ("agent-log",),
        ).fetchall()
        assert len(events) == 1
        assert "1" in events[0]["summary"]

    def test_register_different_name_same_project_live_pid_allowed(self, db):
        """Multiple agents may live in the same project directory."""
        import subprocess
        sibling = subprocess.Popen(["sleep", "5"])
        try:
            db.register_agent("agent-one", "/shared/project", pid=os.getpid())
            db.register_agent("agent-two", "/shared/project", pid=sibling.pid)
            assert db.get_agent("agent-one")["pid"] == os.getpid()
            assert db.get_agent("agent-two")["pid"] == sibling.pid
            assert db.get_agent("agent-one")["project_path"] == "/shared/project"
            assert db.get_agent("agent-two")["project_path"] == "/shared/project"
        finally:
            sibling.kill()
            sibling.wait()

    def test_register_different_name_same_project_dead_pid_allowed(self, db):
        db.register_agent("agent-one", "/shared/project", pid=99_999_999)
        db.register_agent("agent-two", "/shared/project", pid=os.getpid())
        assert db.get_agent("agent-two")["project_path"] == "/shared/project"

    def test_register_logs_event(self, db):
        db.register_agent("a1", "/p")
        cur = db._conn.execute(
            "SELECT * FROM events WHERE participant='a1' AND event_type='register'"
        )
        row = cur.fetchone()
        assert row is not None
