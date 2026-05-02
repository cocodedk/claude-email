"""Replies must address the sender that actually sent the inbound, not the
canonical/first AUTHORIZED_SENDER. With multiple senders configured (the
multi-user / alias case), routing every reply to the canonical means alias
senders can write but never receive — exactly the bug surfaced by the
2026-05-02 Android-app smoke test.

These tests pin the contract for every reply path:

  - ``_send_json_reply``      (envelope ack/error/result for JSON inbound)
  - ``send_threaded_reply``   (CLI [Running]/[Result], @agent acks, meta)
  - ``recipient_for_message`` (async result emails relayed by chat_relay)

Tasks remember the actual inbound sender (``origin_from``) so the relay,
which fires later without the inbound message in hand, can still address
the right inbox.
"""
import email.message
import pytest

from src.chat_db import ChatDB
from src.task_queue import TaskQueue


def _inbound(from_addr: str, msg_id: str = "<m@x>") -> email.message.EmailMessage:
    m = email.message.EmailMessage()
    m["From"] = from_addr
    m["Return-Path"] = f"<{from_addr}>"
    m["Subject"] = "ping"
    m["Message-ID"] = msg_id
    m.set_content("body")
    return m


def _multi_sender_config() -> dict:
    """Canonical + one alias, both authorized."""
    return {
        "smtp_host": "smtp.example.com", "smtp_port": 465,
        "username": "claude@example.com", "password": "pw",
        "authorized_sender": "bb@example.com",
        "authorized_senders": ["bb@example.com", "alias@example.com"],
        "email_domain": "example.com",
    }


class TestJsonReplyAddressing:
    def test_alias_inbound_replies_to_alias(self, mocker):
        from src.json_handler import _send_json_reply
        mock = mocker.patch(
            "src.json_handler.send_reply", return_value="<env-r@x>",
        )
        cfg = _multi_sender_config()
        cfg["reply_to"] = "alias@example.com"
        _send_json_reply(cfg, _inbound("alias@example.com"), '{"v":1}')
        assert mock.call_args.kwargs["to"] == "alias@example.com"

    def test_canonical_inbound_replies_to_canonical(self, mocker):
        from src.json_handler import _send_json_reply
        mock = mocker.patch(
            "src.json_handler.send_reply", return_value="<env-r@x>",
        )
        cfg = _multi_sender_config()
        cfg["reply_to"] = "bb@example.com"
        _send_json_reply(cfg, _inbound("bb@example.com"), '{"v":1}')
        assert mock.call_args.kwargs["to"] == "bb@example.com"

    def test_missing_reply_to_falls_back_to_canonical(self, mocker):
        from src.json_handler import _send_json_reply
        mock = mocker.patch(
            "src.json_handler.send_reply", return_value="<env-r@x>",
        )
        _send_json_reply(_multi_sender_config(), _inbound("bb@example.com"), '{"v":1}')
        assert mock.call_args.kwargs["to"] == "bb@example.com"


class TestTaskOriginFrom:
    """Async result deliveries (relay_outbound_messages) don't have the
    inbound message; they look up tasks.origin_from to know who to
    address."""

    def test_enqueue_persists_origin_from(self, tmp_path):
        ChatDB(str(tmp_path / "x.db"))
        tq = TaskQueue(str(tmp_path / "x.db"))
        tid = tq.enqueue(
            "/p", "do work", origin_from="alias@example.com",
        )
        assert tq.get(tid)["origin_from"] == "alias@example.com"

    def test_enqueue_default_origin_from_is_null(self, tmp_path):
        ChatDB(str(tmp_path / "x.db"))
        tq = TaskQueue(str(tmp_path / "x.db"))
        tid = tq.enqueue("/p", "do work")
        assert tq.get(tid)["origin_from"] is None


class TestRecipientForMessage:
    def test_uses_task_origin_from_when_set(self, tmp_path):
        from src.relay_routing import recipient_for_message
        ChatDB(str(tmp_path / "x.db"))
        tq = TaskQueue(str(tmp_path / "x.db"))
        cdb = ChatDB(str(tmp_path / "x.db"))
        tid = tq.enqueue("/p", "x", origin_from="alias@example.com")
        msg = {"task_id": tid, "from_name": "agent-x"}
        # No universes / aliases configured — the only source for the
        # alias address is tasks.origin_from.
        cfg = {"authorized_sender": "bb@example.com", "universes": []}
        assert recipient_for_message(cdb, msg, cfg) == "alias@example.com"

    def test_falls_back_to_universe_then_canonical(self, tmp_path):
        from src.relay_routing import recipient_for_message
        ChatDB(str(tmp_path / "x.db"))
        tq = TaskQueue(str(tmp_path / "x.db"))
        cdb = ChatDB(str(tmp_path / "x.db"))
        tid = tq.enqueue("/p", "x")  # no origin_from
        msg = {"task_id": tid, "from_name": "agent-x"}
        cfg = {"authorized_sender": "bb@example.com", "universes": []}
        assert recipient_for_message(cdb, msg, cfg) == "bb@example.com"
