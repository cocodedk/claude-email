"""Tests for the shared SQLite database layer (ChatDB)."""
import asyncio
import os
import sqlite3
import pytest
from src.chat_db import ChatDB, AgentNameTaken


@pytest.fixture
def db(tmp_path):
    return ChatDB(str(tmp_path / "test.db"))


class TestSchema:
    def test_wal_mode_enabled(self, db):
        cur = db._conn.execute("PRAGMA journal_mode")
        assert cur.fetchone()[0] == "wal"

    def test_busy_timeout_set(self, db):
        cur = db._conn.execute("PRAGMA busy_timeout")
        assert cur.fetchone()[0] == 5000

    def test_foreign_keys_enabled(self, db):
        cur = db._conn.execute("PRAGMA foreign_keys")
        assert cur.fetchone()[0] == 1

    def test_row_factory_is_row(self, db):
        assert db._conn.row_factory == sqlite3.Row

    def test_tables_exist(self, db):
        cur = db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        names = [r[0] for r in cur.fetchall()]
        assert "agents" in names
        assert "events" in names
        assert "messages" in names

    def test_reopen_existing_db(self, tmp_path):
        path = str(tmp_path / "reopen.db")
        db1 = ChatDB(path)
        db1.register_agent("a1", "/tmp/a1")
        db2 = ChatDB(path)
        assert db2.get_agent("a1") is not None


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


    def test_reap_dead_agents_marks_disconnected(self, db):
        db.register_agent("agent-alive", "/p1")
        db.update_agent_pid("agent-alive", 99999999)  # PID that doesn't exist
        reaped = db.reap_dead_agents()
        assert reaped == ["agent-alive"]
        agent = db.get_agent("agent-alive")
        assert agent["status"] == "disconnected"

    def test_reap_dead_agents_skips_no_pid(self, db):
        db.register_agent("agent-nopid", "/p2")
        # pid is NULL — should not be reaped
        reaped = db.reap_dead_agents()
        assert reaped == []

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


class TestRegisterAgentTransactionFallback:
    """When BEGIN IMMEDIATE fails, register_agent must still work via the
    non-atomic fallback path — the ON CONFLICT clause serialises the write."""

    def test_begin_immediate_failure_falls_back(self, tmp_path):
        import os as _os
        import sqlite3 as _sqlite3

        class _FakeConn:
            """Proxy that raises on BEGIN IMMEDIATE, delegates everything else."""

            def __init__(self, real):
                self._real = real

            def __getattr__(self, attr):
                return getattr(self._real, attr)

            def execute(self, sql, *args, **kwargs):
                if (
                    isinstance(sql, str)
                    and sql.strip().upper().startswith("BEGIN IMMEDIATE")
                ):
                    raise _sqlite3.OperationalError(
                        "cannot start a transaction within a transaction",
                    )
                return self._real.execute(sql, *args, **kwargs)

        db = ChatDB(str(tmp_path / "fallback.db"))
        db._conn = _FakeConn(db._conn)
        # Should still succeed via the fallback path.
        result = db.register_agent("agent-x", "/tmp", pid=_os.getpid())
        assert result["name"] == "agent-x"
        assert result["pid"] == _os.getpid()


class TestMessages:
    def test_insert_message_returns_dict(self, db):
        msg = db.insert_message("alice", "bob", "hello", "ask")
        assert isinstance(msg, dict)
        assert msg["from_name"] == "alice"
        assert msg["to_name"] == "bob"
        assert msg["body"] == "hello"
        assert msg["type"] == "ask"
        assert msg["status"] == "pending"
        assert msg["id"] is not None

    def test_insert_message_with_reply(self, db):
        m1 = db.insert_message("a", "b", "question", "ask")
        m2 = db.insert_message("b", "a", "answer", "reply", in_reply_to=m1["id"])
        assert m2["in_reply_to"] == m1["id"]

    def test_insert_message_logs_event(self, db):
        db.insert_message("a", "b", "hi", "notify")
        cur = db._conn.execute(
            "SELECT * FROM events WHERE participant='a' AND event_type='message'"
        )
        assert cur.fetchone() is not None

    def test_get_pending_messages_fifo(self, db):
        db.insert_message("a", "bob", "first", "ask")
        db.insert_message("a", "bob", "second", "ask")
        db.insert_message("a", "other", "not for bob", "ask")
        pending = db.get_pending_messages_for("bob")
        assert len(pending) == 2
        assert pending[0]["body"] == "first"
        assert pending[1]["body"] == "second"

    def test_mark_message_delivered(self, db):
        msg = db.insert_message("a", "b", "hi", "ask")
        db.mark_message_delivered(msg["id"])
        pending = db.get_pending_messages_for("b")
        assert len(pending) == 0

    def test_mark_message_failed(self, db):
        msg = db.insert_message("a", "b", "hi", "ask")
        db.mark_message_failed(msg["id"])
        # Failed messages are not pending (won't be retried)
        assert db.get_pending_messages_for("b") == []
        row = db._conn.execute(
            "SELECT status FROM messages WHERE id=?", (msg["id"],)
        ).fetchone()
        assert row["status"] == "failed"

    def test_set_email_message_id(self, db):
        msg = db.insert_message("a", "b", "hi", "ask")
        db.set_email_message_id(msg["id"], "<abc@example.com>")
        found = db.find_message_by_email_id("<abc@example.com>")
        assert found is not None
        assert found["id"] == msg["id"]

    def test_find_message_by_email_id_missing(self, db):
        assert db.find_message_by_email_id("<missing@x>") is None


class TestClaimPendingMessages:
    def test_claim_returns_pending_messages_fifo(self, db):
        db.insert_message("a", "bob", "first", "ask")
        db.insert_message("a", "bob", "second", "ask")
        db.insert_message("a", "other", "not for bob", "ask")
        claimed = db.claim_pending_messages_for("bob")
        assert len(claimed) == 2
        assert claimed[0]["body"] == "first"
        assert claimed[1]["body"] == "second"

    def test_claim_marks_messages_delivered(self, db):
        db.insert_message("a", "bob", "hi", "ask")
        db.claim_pending_messages_for("bob")
        assert db.get_pending_messages_for("bob") == []

    def test_claim_second_call_returns_empty(self, db):
        db.insert_message("a", "bob", "hi", "ask")
        db.claim_pending_messages_for("bob")
        assert db.claim_pending_messages_for("bob") == []

    def test_claim_does_not_affect_other_recipients(self, db):
        db.insert_message("a", "alice", "for alice", "ask")
        db.insert_message("a", "bob", "for bob", "ask")
        db.claim_pending_messages_for("bob")
        assert len(db.get_pending_messages_for("alice")) == 1

    def test_claim_returns_empty_when_no_pending(self, db):
        assert db.claim_pending_messages_for("nobody") == []

    def test_claim_sets_status_to_delivered_in_db(self, db):
        msg = db.insert_message("a", "bob", "hi", "ask")
        db.claim_pending_messages_for("bob")
        row = db._conn.execute(
            "SELECT status FROM messages WHERE id=?", (msg["id"],)
        ).fetchone()
        assert row["status"] == "delivered"


class TestWakeNudge:
    def test_insert_message_sets_registered_nudge(self, db):
        nudge = asyncio.Event()
        db.set_wake_nudge(nudge)
        assert not nudge.is_set()
        db.insert_message("a", "b", "hi", "notify")
        assert nudge.is_set()

    def test_insert_message_without_nudge_does_not_raise(self, db):
        db.insert_message("a", "b", "hi", "notify")
        assert db.get_pending_messages_for("b") != []

    def test_nudge_fires_on_every_insert(self, db):
        nudge = asyncio.Event()
        db.set_wake_nudge(nudge)
        db.insert_message("a", "b", "one", "notify")
        assert nudge.is_set()
        nudge.clear()
        db.insert_message("a", "b", "two", "notify")
        assert nudge.is_set()

    def test_get_reply_to_message(self, db):
        ask = db.insert_message("a", "b", "question?", "ask")
        reply = db.insert_message("b", "a", "answer!", "reply", in_reply_to=ask["id"])
        found = db.get_reply_to_message(ask["id"])
        assert found is not None
        assert found["id"] == reply["id"]

    def test_get_reply_to_message_ignores_non_reply_types(self, db):
        ask = db.insert_message("a", "b", "question?", "ask")
        # A command referencing the same in_reply_to should NOT be returned
        db.insert_message("b", "a", "command body", "command", in_reply_to=ask["id"])
        assert db.get_reply_to_message(ask["id"]) is None

    def test_get_reply_to_message_returns_latest(self, db):
        ask = db.insert_message("a", "b", "question?", "ask")
        db.insert_message("b", "a", "first reply", "reply", in_reply_to=ask["id"])
        second = db.insert_message("b", "a", "second reply", "reply", in_reply_to=ask["id"])
        found = db.get_reply_to_message(ask["id"])
        assert found["id"] == second["id"]
        assert found["body"] == "second reply"

    def test_get_reply_to_message_none(self, db):
        ask = db.insert_message("a", "b", "q?", "ask")
        assert db.get_reply_to_message(ask["id"]) is None

    def test_get_last_email_message_id_for_agent(self, db):
        m1 = db.insert_message("agent-foo", "user", "msg1", "notify")
        db.set_email_message_id(m1["id"], "<first@example.com>")
        m2 = db.insert_message("agent-foo", "user", "msg2", "ask")
        db.set_email_message_id(m2["id"], "<second@example.com>")
        assert db.get_last_email_message_id_for_agent("agent-foo") == "<second@example.com>"

    def test_get_last_email_message_id_for_agent_none(self, db):
        db.insert_message("agent-foo", "user", "no email id", "notify")
        assert db.get_last_email_message_id_for_agent("agent-foo") is None

    def test_fk_constraint_on_in_reply_to(self, db):
        with pytest.raises(Exception):
            db.insert_message("a", "b", "bad", "reply", in_reply_to=99999)


class TestCleanupOld:
    def _backdate(self, db, table: str, row_id: int, days_ago: int) -> None:
        from datetime import datetime, timedelta, timezone
        ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
        db._conn.execute(f"UPDATE {table} SET created_at=? WHERE id=?", (ts, row_id))
        db._conn.commit()

    def test_deletes_old_delivered_messages(self, db):
        old = db.insert_message("a", "b", "old", "chat")
        db.mark_message_delivered(old["id"])
        self._backdate(db, "messages", old["id"], days_ago=60)

        result = db.cleanup_old(days=30)
        assert result["messages"] == 1
        assert db._conn.execute(
            "SELECT 1 FROM messages WHERE id=?", (old["id"],)
        ).fetchone() is None

    def test_deletes_old_failed_messages(self, db):
        old = db.insert_message("a", "b", "old", "chat")
        db.mark_message_failed(old["id"])
        self._backdate(db, "messages", old["id"], days_ago=60)

        result = db.cleanup_old(days=30)
        assert result["messages"] == 1

    def test_keeps_recent_messages(self, db):
        recent = db.insert_message("a", "b", "recent", "chat")
        db.mark_message_delivered(recent["id"])
        # Not backdated — created_at is now

        result = db.cleanup_old(days=30)
        assert result["messages"] == 0
        assert db._conn.execute(
            "SELECT 1 FROM messages WHERE id=?", (recent["id"],)
        ).fetchone() is not None

    def test_keeps_pending_even_if_old(self, db):
        """Never delete pending messages — they may still need delivery."""
        stuck = db.insert_message("a", "b", "stuck", "chat")
        self._backdate(db, "messages", stuck["id"], days_ago=365)

        result = db.cleanup_old(days=30)
        assert result["messages"] == 0
        assert db.get_pending_messages_for("b")[0]["id"] == stuck["id"]

    def test_deletes_old_events(self, db):
        # Register + insert_message create events; backdate them
        db.register_agent("a1", "/p")
        rows = db._conn.execute("SELECT id FROM events").fetchall()
        assert rows
        for r in rows:
            self._backdate(db, "events", r["id"], days_ago=60)

        result = db.cleanup_old(days=30)
        assert result["events"] >= 1
