"""Tests for the shared SQLite database layer (ChatDB) — reap_dead_agents."""
import pytest
from src.chat_db import ChatDB


@pytest.fixture
def db(tmp_path):
    return ChatDB(str(tmp_path / "test.db"))


class TestAgentsReap:
    def test_reap_dead_agents_marks_disconnected(self, db):
        db.register_agent("agent-alive", "/p1")
        db.update_agent_pid("agent-alive", 99999999)  # PID that doesn't exist
        reaped = db.reap_dead_agents()
        assert reaped == ["agent-alive"]
        agent = db.get_agent("agent-alive")
        assert agent["status"] == "disconnected"

    def test_reap_dead_agents_skips_no_pid_when_fresh(self, db):
        db.register_agent("agent-nopid", "/p2")
        # pid is NULL but row was just registered (last_seen_at = now) —
        # protected from the no-pid sweep so live MCP-only agents survive.
        reaped = db.reap_dead_agents()
        assert reaped == []

    def test_reap_dead_agents_reaps_no_pid_when_stale(self, db):
        from datetime import datetime, timedelta, timezone
        db.register_agent("agent-ghost-nopid", "/p-ghost")
        stale = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        db._conn.execute(
            "UPDATE agents SET last_seen_at=? WHERE name=?",
            (stale, "agent-ghost-nopid"),
        )
        db._conn.commit()
        reaped = db.reap_dead_agents()
        assert reaped == ["agent-ghost-nopid"]
        assert db.get_agent("agent-ghost-nopid")["status"] == "disconnected"

    def test_reap_dead_agents_no_pid_threshold_configurable(self, db):
        from datetime import datetime, timedelta, timezone
        db.register_agent("agent-mid", "/p-mid")
        # 10s old — stale under 5s threshold, fresh under 60s threshold.
        ten_sec_ago = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        db._conn.execute(
            "UPDATE agents SET last_seen_at=? WHERE name=?",
            (ten_sec_ago, "agent-mid"),
        )
        db._conn.commit()
        assert db.reap_dead_agents(no_pid_idle_secs=60) == []
        assert db.reap_dead_agents(no_pid_idle_secs=5) == ["agent-mid"]

    def test_reap_dead_agents_skips_already_disconnected(self, db):
        db.register_agent("agent-disc", "/p3")
        db.update_agent_pid("agent-disc", 99999999)
        db.update_agent_status("agent-disc", "disconnected")
        reaped = db.reap_dead_agents()
        assert reaped == []

    def test_reap_dead_agents_reaps_zombie_children(self, db):
        """A zombie child (exited, not wait()'d) must be reaped.

        os.kill(pid, 0) returns success on zombies because the PID is still
        in the process table — so a naive kill-based liveness probe leaves
        the agent stuck at status='running' forever. The fix uses
        os.waitpid(pid, WNOHANG) first to reap zombies we parented, and
        falls back to os.kill(pid, 0) for non-child PIDs.
        """
        import os
        import subprocess
        import time

        child = subprocess.Popen(["true"])
        pid = child.pid
        # Poll until it's a zombie (has exited, not yet reaped)
        state = None
        for _ in range(100):
            time.sleep(0.01)
            try:
                with open(f"/proc/{pid}/stat") as f:
                    state = f.read().split()[2]
            except FileNotFoundError:
                state = None
                break
            if state == "Z":
                break
        assert state == "Z", f"failed to produce a zombie for test (state={state!r})"

        # Confirm kill(0) succeeds on the zombie — this is the bug driver
        os.kill(pid, 0)  # must not raise

        db.register_agent("agent-zombie", "/p")
        db.update_agent_pid("agent-zombie", pid)

        reaped = db.reap_dead_agents()
        assert reaped == ["agent-zombie"]
        assert db.get_agent("agent-zombie")["status"] == "disconnected"
        # After waitpid, the zombie should no longer exist in /proc
        assert not os.path.exists(f"/proc/{pid}"), (
            "zombie still present in /proc — reap did not call waitpid"
        )

    def test_reap_dead_agents_leaves_live_children_alone(self, db):
        """A long-running child must NOT be marked disconnected."""
        import subprocess
        child = subprocess.Popen(["sleep", "5"])
        try:
            db.register_agent("agent-running", "/p")
            db.update_agent_pid("agent-running", child.pid)
            reaped = db.reap_dead_agents()
            assert reaped == []
            assert db.get_agent("agent-running")["status"] == "running"
        finally:
            child.kill()
            child.wait()

    def test_reap_dead_agents_handles_non_child_dead_pid(self, db):
        """If the stored PID was never our child and now doesn't exist,
        kill(0) raises OSError and we still mark the agent disconnected."""
        db.register_agent("agent-ghost", "/p")
        db.update_agent_pid("agent-ghost", 99999999)  # not our child, not live
        reaped = db.reap_dead_agents()
        assert reaped == ["agent-ghost"]
        assert db.get_agent("agent-ghost")["status"] == "disconnected"
