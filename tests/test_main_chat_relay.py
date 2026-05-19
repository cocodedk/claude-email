"""Tests for outbound relay in src/chat_handlers.py — subject symmetry + delivery."""
import email.message
import tempfile
import os
import pytest

from src.chat_db import ChatDB
from tests._main_chat_helpers import _make_config, _make_msg, chat_db


class TestRelaySubjectSymmetry:
    """RESULT subject must carry the inbound identifier tag so the client
    canary (ack.subject contains X ⇒ result.subject contains X) passes.
    Symmetric with the ACK path, which reuses original_message.Subject
    via send_threaded_reply."""

    def test_relay_uses_origin_subject_for_task_linked_json(self, mocker, tmp_path):
        """JSON-origin RESULT email: subject = task.origin_subject verbatim,
        no [from_name] template, no extra tag prepend."""
        from src.chat_handlers import relay_outbound_messages
        from src.task_queue import TaskQueue

        db_path = str(tmp_path / "sym.db")
        cdb = ChatDB(db_path)
        tq = TaskQueue(db_path)
        tid = tq.enqueue(
            "/p", "do it", origin_content_type="application/json",
            origin_message_id="<inbound-json@x>",
            origin_subject="[test-0042] do it",
        )
        cdb.insert_message(
            "agent-p", "user", '{"v":1,"kind":"result"}', "notify",
            content_type="application/json", task_id=tid,
        )
        mock_send = mocker.patch("src.chat_relay.send_reply", return_value="<r@x>")
        relay_outbound_messages(_make_config(), cdb)
        subject = mock_send.call_args.kwargs["subject"]
        assert subject == "[test-0042] do it"

    def test_relay_uses_origin_subject_with_tag_for_plain_text(self, mocker, tmp_path):
        """Plain-text RESULT email: subject = [Update] <origin_subject>
        so the identifier survives but the type-tag still hints at intent."""
        from src.chat_handlers import relay_outbound_messages
        from src.task_queue import TaskQueue

        db_path = str(tmp_path / "sym.db")
        cdb = ChatDB(db_path)
        tq = TaskQueue(db_path)
        tid = tq.enqueue(
            "/p", "do it",
            origin_message_id="<inbound-pt@x>",
            origin_subject="[task-7] do it",
        )
        cdb.insert_message(
            "agent-p", "user", "done!", "notify", task_id=tid,
        )
        mock_send = mocker.patch("src.chat_relay.send_reply", return_value="<r@x>")
        relay_outbound_messages(_make_config(), cdb)
        subject = mock_send.call_args.kwargs["subject"]
        assert "[task-7]" in subject
        assert "[Update]" in subject

    def test_relay_falls_back_to_template_when_no_origin_subject(self, mocker, chat_db):
        """Backward-compat: old task rows without origin_subject still get
        the [from_name] message subject template. Email-origin context is
        provided via a prior user→agent command so the relay gate accepts."""
        from src.chat_handlers import relay_outbound_messages

        chat_db.insert_message("user", "agent-solo", "kick off", "command")
        chat_db.insert_message("agent-solo", "user", "hi", "notify")
        mock_send = mocker.patch("src.chat_relay.send_reply", return_value="<r@x>")
        relay_outbound_messages(_make_config(), chat_db)
        subject = mock_send.call_args.kwargs["subject"]
        assert "agent-solo" in subject
        assert "message" in subject


class TestRelayOutboundMessages:
    def test_relay_outbound_messages(self, mocker, chat_db):
        """Pending agent messages get sent as emails and marked delivered."""
        from src.chat_handlers import relay_outbound_messages

        mock_reply = mocker.patch("src.chat_relay.send_reply", return_value="<test@example.com>")

        # Email-origin context: user previously emailed @agent-foo and
        # @agent-bar, so the gate accepts subsequent notify replies.
        chat_db.insert_message("user", "agent-foo", "go", "command")
        chat_db.insert_message("user", "agent-bar", "go", "command")
        chat_db.insert_message("agent-foo", "user", "Build succeeded!", "chat")
        chat_db.insert_message("agent-bar", "user", "Tests all pass", "chat")

        config = _make_config()
        relay_outbound_messages(config, chat_db)

        assert mock_reply.call_count == 2
        # Both should now be delivered
        assert chat_db.get_pending_messages_for("user") == []

    def test_relay_marks_failed_on_permanent_smtp_error(self, mocker, chat_db):
        """Permanent SMTP errors (auth, bad recipient) mark the message failed, no retry."""
        import smtplib
        from src.chat_handlers import relay_outbound_messages

        mocker.patch(
            "src.chat_relay.send_reply",
            side_effect=smtplib.SMTPRecipientsRefused({"x@y": (550, b"no such user")}),
        )

        chat_db.insert_message("user", "agent-foo", "go", "command")
        msg = chat_db.insert_message("agent-foo", "user", "Build succeeded!", "chat")
        config = _make_config()
        relay_outbound_messages(config, chat_db)

        # Message should NOT be pending (won't retry) — must be marked failed
        assert chat_db.get_pending_messages_for("user") == []
        row = chat_db._conn.execute(
            "SELECT status FROM messages WHERE id=?", (msg["id"],)
        ).fetchone()
        assert row["status"] == "failed"

    def test_relay_keeps_pending_on_transient_smtp_error(self, mocker, chat_db):
        """Transient errors (connection drop, timeout) keep message pending for retry."""
        import smtplib
        from src.chat_handlers import relay_outbound_messages

        mocker.patch(
            "src.chat_relay.send_reply",
            side_effect=smtplib.SMTPServerDisconnected("connection lost"),
        )

        chat_db.insert_message("user", "agent-foo", "go", "command")
        msg = chat_db.insert_message("agent-foo", "user", "Build succeeded!", "chat")
        config = _make_config()
        relay_outbound_messages(config, chat_db)

        # Still pending — will retry next loop
        pending = chat_db.get_pending_messages_for("user")
        assert len(pending) == 1
        assert pending[0]["id"] == msg["id"]

    def test_relay_stops_after_transient_to_avoid_hammering(self, mocker, chat_db):
        """On transient SMTP failure, stop iterating — don't hammer broken connection."""
        import smtplib
        from src.chat_handlers import relay_outbound_messages

        mock_reply = mocker.patch(
            "src.chat_relay.send_reply",
            side_effect=smtplib.SMTPServerDisconnected("connection lost"),
        )

        chat_db.insert_message("user", "agent-foo", "go", "command")
        chat_db.insert_message("user", "agent-bar", "go", "command")
        chat_db.insert_message("agent-foo", "user", "msg1", "chat")
        chat_db.insert_message("agent-bar", "user", "msg2", "chat")

        config = _make_config()
        relay_outbound_messages(config, chat_db)

        # Only one send attempt — we bail on first transient failure
        assert mock_reply.call_count == 1
        # Both still pending
        assert len(chat_db.get_pending_messages_for("user")) == 2
