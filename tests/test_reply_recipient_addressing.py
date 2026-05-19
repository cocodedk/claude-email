"""Reply-addressing tests: the ``_send_json_reply`` / task ``origin_from``
/ ``recipient_for_message`` contracts that ensure alias senders receive
their replies instead of routing every reply to the canonical sender.

Split out of ``tests/test_reply_recipient.py`` (the original module
docstring lives in ``tests/_reply_recipient_helpers.py``).
"""
import pytest

from src.chat_db import ChatDB
from src.task_queue import TaskQueue
from tests._reply_recipient_helpers import _inbound, _multi_sender_config


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
