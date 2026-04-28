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


class TestCleanupLogIncludesOutbound:
    """Regression: the periodic cleanup log gate ignored ``outbound_emails``,
    so a cleanup pass that pruned only stale outbound IDs (with no
    matching delivered/failed messages) silently logged nothing. Catches
    the asymmetry between the columns cleanup writes and the columns
    the operator-visible log surfaces."""

    def test_log_fires_when_only_outbound_rows_pruned(self, cdb, caplog):
        import logging
        from datetime import datetime, timedelta, timezone
        from src.chat_relay import maybe_cleanup_db

        old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
        cdb.record_outbound_email("<aged@x>", kind="ack")
        cdb._conn.execute(
            "UPDATE outbound_emails SET sent_at=? WHERE email_message_id=?",
            (old, "<aged@x>"),
        )
        cdb._conn.commit()

        # Force the cleanup interval gate open.
        import src.chat_relay as cr
        cr._last_cleanup_ts = 0.0

        with caplog.at_level(logging.INFO, logger="src.chat_relay"):
            maybe_cleanup_db(cdb)

        assert any(
            "outbound IDs" in r.message and "DB cleanup" in r.message
            for r in caplog.records
        )


