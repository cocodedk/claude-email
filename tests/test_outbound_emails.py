"""Tests for the outbound_emails Message-ID recorder.

Replies into the service auth themselves via thread-match — security
checks the inbound email's In-Reply-To header against IDs we previously
issued. Until now only the relay's outbound asks/notifies were stored
(in messages.email_message_id), so replies to CLI-fallback [Running]/
[Result] mails, JSON envelope responses, and @agent ACKs were rejected
as 'no chat-thread match'. The outbound_emails table is the single
source of truth for "did we send this Message-ID?".
"""
import pytest

from src.chat_db import ChatDB


@pytest.fixture
def cdb(tmp_path):
    return ChatDB(str(tmp_path / "outbound.db"))


class TestRecordOutbound:
    def test_record_then_find(self, cdb):
        cdb.record_outbound_email(
            "<m1@cocode.dk>", kind="ack", sender_agent="agent-x",
        )
        row = cdb.find_outbound_email("<m1@cocode.dk>")
        assert row is not None
        assert row["email_message_id"] == "<m1@cocode.dk>"
        assert row["kind"] == "ack"
        assert row["sender_agent"] == "agent-x"

    def test_find_unknown_returns_none(self, cdb):
        assert cdb.find_outbound_email("<never-sent@x>") is None

    def test_blank_id_is_rejected(self, cdb):
        """Don't pollute the lookup with empty rows; mailer should never
        return an empty Message-ID, but defend in depth."""
        with pytest.raises(ValueError):
            cdb.record_outbound_email("", kind="ack")
        assert cdb.find_outbound_email("") is None

    def test_duplicate_id_is_idempotent(self, cdb):
        cdb.record_outbound_email("<m1@x>", kind="ack")
        cdb.record_outbound_email("<m1@x>", kind="result")  # second send same ID
        row = cdb.find_outbound_email("<m1@x>")
        assert row is not None  # no IntegrityError, no duplicate row

    def test_cleanup_prunes_old_rows(self, cdb):
        from datetime import datetime, timedelta, timezone
        old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
        cdb.record_outbound_email("<old@x>", kind="ack")
        cdb._conn.execute(
            "UPDATE outbound_emails SET sent_at=? WHERE email_message_id=?",
            (old, "<old@x>"),
        )
        cdb._conn.commit()
        cdb.record_outbound_email("<fresh@x>", kind="ack")

        result = cdb.cleanup_old(days=30)

        assert result["outbound_emails"] >= 1
        assert cdb.find_outbound_email("<old@x>") is None
        assert cdb.find_outbound_email("<fresh@x>") is not None


class TestSendAndRecord:
    """The mailer wrapper that every outbound site routes through. It
    forwards send-args to mailer.send_reply and persists the returned
    Message-ID into outbound_emails on success."""

    def test_records_returned_message_id(self, cdb, mocker):
        from src.mailer import send_and_record
        mocker.patch("src.mailer.send_reply", return_value="<sent-1@cocode.dk>")

        rv = send_and_record(
            cdb, kind="ack", sender_agent="agent-x",
            smtp_host="h", smtp_port=465, username="u", password="p",
            to="bb@cocode.dk", subject="S", body="b",
        )

        assert rv == "<sent-1@cocode.dk>"
        row = cdb.find_outbound_email("<sent-1@cocode.dk>")
        assert row is not None
        assert row["kind"] == "ack"
        assert row["sender_agent"] == "agent-x"

    def test_send_failure_does_not_record(self, cdb, mocker):
        import smtplib
        from src.mailer import send_and_record
        mocker.patch(
            "src.mailer.send_reply",
            side_effect=smtplib.SMTPException("boom"),
        )

        with pytest.raises(smtplib.SMTPException):
            send_and_record(
                cdb, kind="ack",
                smtp_host="h", smtp_port=465, username="u", password="p",
                to="bb@cocode.dk", subject="S", body="b",
            )

        assert cdb.find_outbound_email("<anything@x>") is None

    def test_blank_message_id_skipped(self, cdb, mocker):
        """If send_reply returns "" (defensive — make_msgid always returns
        a value, but be safe), no row is recorded but the call still
        returns the value to the caller."""
        from src.mailer import send_and_record
        mocker.patch("src.mailer.send_reply", return_value="")

        rv = send_and_record(
            cdb, kind="ack",
            smtp_host="h", smtp_port=465, username="u", password="p",
            to="bb@cocode.dk", subject="S", body="b",
        )
        assert rv == ""
        assert cdb._conn.execute(
            "SELECT COUNT(*) FROM outbound_emails"
        ).fetchone()[0] == 0
