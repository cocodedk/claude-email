"""Tests for ChatDB read-only dashboard queries (DashboardQueriesMixin)."""
import pytest

from src.chat_db import ChatDB


@pytest.fixture
def db(tmp_path):
    return ChatDB(str(tmp_path / "test.db"))


class TestMessagesSummary:
    def test_empty(self, db):
        assert db.get_messages_summary() == []

    def test_returns_all_fields(self, db):
        db.insert_message("alice", "bob", "hi", "notify")
        [row] = db.get_messages_summary()
        assert row["from_name"] == "alice"
        assert row["to_name"] == "bob"
        assert row["body"] == "hi"
        assert row["type"] == "notify"
        assert row["status"] == "pending"
        assert "created_at" in row
        assert "id" in row

    def test_orders_newest_first(self, db):
        db.insert_message("a", "b", "one", "notify")
        db.insert_message("a", "b", "two", "notify")
        rows = db.get_messages_summary()
        assert rows[0]["body"] == "two"
        assert rows[1]["body"] == "one"

    def test_respects_limit(self, db):
        for i in range(5):
            db.insert_message("a", "b", f"msg-{i}", "notify")
        assert len(db.get_messages_summary(limit=2)) == 2
        assert len(db.get_messages_summary(limit=100)) == 5


class TestMessagesSince:
    def test_empty(self, db):
        assert db.get_messages_since(0) == []

    def test_returns_rows_after_watermark(self, db):
        m1 = db.insert_message("a", "b", "one", "notify")
        m2 = db.insert_message("a", "b", "two", "notify")
        m3 = db.insert_message("a", "b", "three", "notify")
        rows = db.get_messages_since(m1["id"])
        assert [r["id"] for r in rows] == [m2["id"], m3["id"]]

    def test_orders_ascending(self, db):
        for i in range(3):
            db.insert_message("a", "b", f"m{i}", "notify")
        rows = db.get_messages_since(0)
        ids = [r["id"] for r in rows]
        assert ids == sorted(ids)

    def test_respects_limit(self, db):
        for i in range(5):
            db.insert_message("a", "b", f"m{i}", "notify")
        rows = db.get_messages_since(0, limit=2)
        assert len(rows) == 2


class TestLatestMessageId:
    def test_empty_returns_zero(self, db):
        assert db.latest_message_id() == 0

    def test_returns_max_id(self, db):
        db.insert_message("a", "b", "first", "notify")
        latest = db.insert_message("a", "b", "last", "notify")
        assert db.latest_message_id() == latest["id"]
