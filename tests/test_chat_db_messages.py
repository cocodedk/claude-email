"""Tests for the shared SQLite database layer (ChatDB) — messages + claim."""
import pytest
from src.chat_db import ChatDB


@pytest.fixture
def db(tmp_path):
    return ChatDB(str(tmp_path / "test.db"))


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

    def test_recover_failed_messages_for_flips_only_failed_to_pending(
        self, db,
    ):
        """Idempotent: delivered/pending must not be touched (only failed flips)."""
        delivered = db.insert_message("a", "agent-x", "delivered", "notify")
        pending = db.insert_message("a", "agent-x", "pending", "notify")
        failed = db.insert_message("a", "agent-x", "failed", "notify")
        db.mark_message_delivered(delivered["id"])
        db.mark_message_failed(failed["id"])

        db.recover_failed_messages_for("agent-x")

        statuses = {
            row["id"]: row["status"]
            for row in db._conn.execute(
                "SELECT id, status FROM messages WHERE to_name=?",
                ("agent-x",),
            ).fetchall()
        }
        assert statuses[delivered["id"]] == "delivered"
        assert statuses[pending["id"]] == "pending"
        assert statuses[failed["id"]] == "pending"

    def test_recover_failed_messages_for_returns_count(self, db):
        """register_agent uses the count to gate the messages_recovered event."""
        assert db.recover_failed_messages_for("agent-y") == 0

        m1 = db.insert_message("a", "agent-y", "one", "notify")
        m2 = db.insert_message("a", "agent-y", "two", "notify")
        db.mark_message_failed(m1["id"])
        db.mark_message_failed(m2["id"])
        assert db.recover_failed_messages_for("agent-y") == 2
        # Idempotent — second call finds nothing to flip.
        assert db.recover_failed_messages_for("agent-y") == 0

    def test_recover_failed_messages_for_scoped_to_agent(self, db):
        """Recovery is per-agent — never resurrects another agent's escalated mail."""
        a = db.insert_message("p", "agent-a", "for-a", "notify")
        b = db.insert_message("p", "agent-b", "for-b", "notify")
        db.mark_message_failed(a["id"])
        db.mark_message_failed(b["id"])

        db.recover_failed_messages_for("agent-a")

        assert db.get_pending_messages_for("agent-a")[0]["body"] == "for-a"
        # Agent-b's failed message still failed.
        assert db.get_pending_messages_for("agent-b") == []

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
