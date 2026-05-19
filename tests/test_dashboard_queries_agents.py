"""Tests for ChatDB read-only dashboard queries (DashboardQueriesMixin)."""
import pytest

from src.chat_db import ChatDB


@pytest.fixture
def db(tmp_path):
    return ChatDB(str(tmp_path / "test.db"))


class TestAgentsSummary:
    def test_empty(self, db):
        assert db.get_agents_summary() == []

    def test_lists_registered_agents(self, db):
        db.register_agent("a1", "/p1")
        db.register_agent("a2", "/p2")
        result = db.get_agents_summary()
        names = {r["name"] for r in result}
        assert names == {"a1", "a2"}

    def test_includes_status_pid_and_last_seen(self, db):
        import os
        my_pid = os.getpid()  # a real, alive pid so the liveness filter keeps it
        db.register_agent("bot", "/p", pid=my_pid)
        [row] = db.get_agents_summary()
        assert row["status"] == "running"
        assert row["pid"] == my_pid
        assert row["project_path"] == "/p"
        assert "last_seen_at" in row

    def test_orders_newest_first(self, db):
        db.register_agent("first", "/p1")
        db.register_agent("second", "/p2")
        names = [r["name"] for r in db.get_agents_summary()]
        # last_seen_at DESC — most recent registration first
        assert names[0] == "second"

    def test_hides_stale_pid_null_agents(self, db):
        """An MCP-registered agent (pid=NULL) that crashed can't be seen
        by is_alive-based reaping, so the dashboard filters it by stale
        heartbeat instead. The DB row stays (ownership logic still works)."""
        db.register_agent("ghost", "/p1")  # pid defaults to NULL
        db._conn.execute(
            "UPDATE agents SET last_seen_at=? WHERE name=?",
            ("1970-01-01T00:00:00+00:00", "ghost"),
        )
        db._conn.commit()
        db.register_agent("fresh", "/p2")
        names = [r["name"] for r in db.get_agents_summary()]
        assert names == ["fresh"]
        assert db.get_agent("ghost") is not None

    def test_shows_live_pid_agents_even_when_stale(self, db):
        """A long-running Claude session that doesn't poll its inbox can
        have an ancient last_seen_at but is very much alive. If its PID is
        alive, the dashboard must show it — the kernel is the ground truth."""
        import os
        db.register_agent("dormant", "/p1", pid=os.getpid())
        # Backdate the heartbeat far past the default threshold.
        db._conn.execute(
            "UPDATE agents SET last_seen_at=? WHERE name=?",
            ("1970-01-01T00:00:00+00:00", "dormant"),
        )
        db._conn.commit()
        [row] = db.get_agents_summary()
        assert row["name"] == "dormant"
        # Status reconciles to 'running' regardless of what the column says.
        assert row["status"] == "running"

    def test_hides_agents_whose_pid_is_dead(self, db):
        """When is_alive(pid) is False, the agent is definitely gone —
        hide immediately, don't wait for reap_dead_agents to flip status."""
        db.register_agent("crashed", "/p1", pid=99999999)  # definitely dead
        assert db.get_agents_summary() == []

    def test_status_column_ignored_when_pid_is_live(self, db):
        """A stale 'disconnected' label on a row whose pid is alive means
        the reaper ran during a brief hang; trust the kernel, not the label."""
        import os
        db.register_agent("revived", "/p1", pid=os.getpid())
        db.update_agent_status("revived", "disconnected")
        [row] = db.get_agents_summary()
        assert row["status"] == "running"

    def test_hides_disconnected_when_pid_is_null(self, db):
        """Without a PID we can't overrule the status column."""
        db.register_agent("goneforgood", "/p1")  # pid=NULL
        db.update_agent_status("goneforgood", "disconnected")
        assert db.get_agents_summary() == []

    def test_stale_threshold_is_configurable(self, db):
        """Callers that want the full picture can pass a huge threshold."""
        db.register_agent("anyone", "/p1")  # pid=NULL so threshold applies
        db._conn.execute(
            "UPDATE agents SET last_seen_at=? WHERE name=?",
            ("1970-01-01T00:00:00+00:00", "anyone"),
        )
        db._conn.commit()
        assert db.get_agents_summary() == []
        assert db.get_agents_summary(stale_secs=3600 * 24 * 365 * 100) != []
