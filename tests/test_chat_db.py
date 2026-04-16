"""Tests for the shared SQLite database layer (ChatDB)."""
import sqlite3
import pytest
from src.chat_db import ChatDB


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

    def test_register_logs_event(self, db):
        db.register_agent("a1", "/p")
        cur = db._conn.execute(
            "SELECT * FROM events WHERE participant='a1' AND event_type='register'"
        )
        row = cur.fetchone()
        assert row is not None


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

    def test_set_email_message_id(self, db):
        msg = db.insert_message("a", "b", "hi", "ask")
        db.set_email_message_id(msg["id"], "<abc@example.com>")
        found = db.find_message_by_email_id("<abc@example.com>")
        assert found is not None
        assert found["id"] == msg["id"]

    def test_find_message_by_email_id_missing(self, db):
        assert db.find_message_by_email_id("<missing@x>") is None

    def test_get_reply_to_message(self, db):
        ask = db.insert_message("a", "b", "question?", "ask")
        reply = db.insert_message("b", "a", "answer!", "reply", in_reply_to=ask["id"])
        found = db.get_reply_to_message(ask["id"])
        assert found is not None
        assert found["id"] == reply["id"]

    def test_get_reply_to_message_none(self, db):
        ask = db.insert_message("a", "b", "q?", "ask")
        assert db.get_reply_to_message(ask["id"]) is None

    def test_fk_constraint_on_in_reply_to(self, db):
        with pytest.raises(Exception):
            db.insert_message("a", "b", "bad", "reply", in_reply_to=99999)
