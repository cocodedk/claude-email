"""Body + in_reply_to_eid persistence for email-thread context.

- record_outbound_email accepts body + in_reply_to_eid kwargs and
  round-trips them via find_outbound_email.
- send_threaded_reply forwards body + parent header to the recorder.
- ChatDB.insert_message accepts in_reply_to_eid kwarg.
"""
import pytest

from src.chat_db import ChatDB
from tests._email_helpers import email_config, inbound_email


@pytest.fixture
def cdb(tmp_path):
    return ChatDB(str(tmp_path / "p.db"))


class TestRecordOutboundEmailExtraColumns:
    def test_persists_body_and_parent_eid(self, cdb):
        cdb.record_outbound_email(
            "<reply-1@example.com>", kind="result",
            body="full router output", in_reply_to_eid="<inbound-1@example.com>",
        )
        row = cdb.find_outbound_email("<reply-1@example.com>")
        assert row["body"] == "full router output"
        assert row["in_reply_to_eid"] == "<inbound-1@example.com>"
        assert row["kind"] == "result"

    def test_body_and_parent_default_to_null(self, cdb):
        cdb.record_outbound_email("<ack-1@example.com>", kind="ack")
        row = cdb.find_outbound_email("<ack-1@example.com>")
        assert row["body"] is None
        assert row["in_reply_to_eid"] is None

    def test_conflict_update_preserves_body(self, cdb):
        # Create a real task so the FK on outbound_emails.task_id holds.
        cdb._conn.execute(
            "INSERT INTO tasks (project_path, body, created_at) "
            "VALUES ('/p', 'x', '2026-05-19T00:00:00+00:00')"
        )
        cdb._conn.commit()
        tid = cdb._conn.execute("SELECT id FROM tasks LIMIT 1").fetchone()["id"]
        cdb.record_outbound_email(
            "<x@example.com>", kind="result",
            body="first", in_reply_to_eid="<p@example.com>",
        )
        cdb.record_outbound_email("<x@example.com>", kind="result", task_id=tid)
        row = cdb.find_outbound_email("<x@example.com>")
        assert row["body"] == "first"
        assert row["in_reply_to_eid"] == "<p@example.com>"
        assert row["task_id"] == tid


class TestSendThreadedReplyForwardsBody:
    def test_persists_body_and_parent(self, cdb, mocker):
        from src.chat_handlers import send_threaded_reply
        mocker.patch(
            "src.chat_handlers.send_reply", return_value="<reply-2@example.com>",
        )
        send_threaded_reply(
            email_config(), inbound_email(msg_id="<inb-2@example.com>"),
            "router said this", tag="Result", chat_db=cdb, kind="result",
        )
        row = cdb.find_outbound_email("<reply-2@example.com>")
        assert row["body"] is not None and "router said this" in row["body"]
        assert row["in_reply_to_eid"] == "<inb-2@example.com>"


class TestInsertMessageInReplyToEid:
    def test_round_trip(self, cdb):
        row = cdb.insert_message(
            "user", "router", "follow-up body", "email_inbound",
            content_type="email/router-turn",
            in_reply_to_eid="<prev@example.com>",
        )
        assert row["in_reply_to_eid"] == "<prev@example.com>"
        fetched = cdb.find_message_by_email_id(row["email_message_id"] or "")
        # email_message_id not set in this test — verify via direct id
        assert cdb.get_message(row["id"])["in_reply_to_eid"] == "<prev@example.com>"
