"""Tests for the shared SQLite database layer (ChatDB) — wake nudge + cleanup."""
import asyncio
import pytest
from src.chat_db import ChatDB


@pytest.fixture
def db(tmp_path):
    return ChatDB(str(tmp_path / "test.db"))


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
