"""Origin-stamping tests for the LLM-router dispatch-token fixup.

Split out of ``tests/test_reply_recipient.py``.
"""
import pytest

from src.chat_db import ChatDB
from src.task_queue import TaskQueue


class TestStampOriginByToken:
    """Tasks created via the LLM-router MCP path carry a per-dispatch
    ``dispatch_token`` so the post-execute fixup can stamp origin_*
    deterministically without window/path heuristics — concurrent
    enqueues from other MCP clients won't carry this dispatch's token,
    so they're left alone, and a single dispatch enqueueing multiple
    tasks stamps them all."""

    def test_stamps_every_task_carrying_token(self, tmp_path):
        from src.reply_routing_fixup import stamp_origin_by_token
        ChatDB(str(tmp_path / "x.db"))
        tq = TaskQueue(str(tmp_path / "x.db"))
        proj = str(tmp_path / "p")
        (tmp_path / "p").mkdir()
        a = tq.enqueue(proj, "task one", dispatch_token="tok-1")
        b = tq.enqueue(proj, "task two", dispatch_token="tok-1")
        n = stamp_origin_by_token(
            db_path=str(tmp_path / "x.db"),
            dispatch_token="tok-1", reply_to="alias@example.com",
            origin_message_id="<m-1@example.com>",
            origin_subject="Re: do work",
        )
        assert n == 2
        for tid in (a, b):
            row = tq.get(tid)
            assert row["origin_from"] == "alias@example.com"
            assert row["origin_message_id"] == "<m-1@example.com>"
            assert row["origin_subject"] == "Re: do work"

    def test_skips_tasks_with_other_tokens(self, tmp_path):
        """Concurrent non-router enqueue carries a different token (or
        no token) and must not be stamped with this dispatch's sender."""
        from src.reply_routing_fixup import stamp_origin_by_token
        ChatDB(str(tmp_path / "x.db"))
        tq = TaskQueue(str(tmp_path / "x.db"))
        mine = tq.enqueue("/p", "router task", dispatch_token="tok-mine")
        other = tq.enqueue("/p", "concurrent task", dispatch_token="tok-other")
        none = tq.enqueue("/p", "tokenless task")
        n = stamp_origin_by_token(
            db_path=str(tmp_path / "x.db"),
            dispatch_token="tok-mine", reply_to="alias@example.com",
        )
        assert n == 1
        assert tq.get(mine)["origin_from"] == "alias@example.com"
        assert tq.get(other)["origin_from"] is None
        assert tq.get(none)["origin_from"] is None

    def test_does_not_overwrite_existing_origin_from(self, tmp_path):
        from src.reply_routing_fixup import stamp_origin_by_token
        ChatDB(str(tmp_path / "x.db"))
        tq = TaskQueue(str(tmp_path / "x.db"))
        tid = tq.enqueue(
            "/p", "x", dispatch_token="tok-1", origin_from="real@example.com",
        )
        n = stamp_origin_by_token(
            db_path=str(tmp_path / "x.db"),
            dispatch_token="tok-1",
            reply_to="should-not-win@example.com",
        )
        assert n == 0
        assert tq.get(tid)["origin_from"] == "real@example.com"

    def test_empty_token_is_noop(self, tmp_path):
        """A blank token would mass-stamp every tokenless task — refuse."""
        from src.reply_routing_fixup import stamp_origin_by_token
        ChatDB(str(tmp_path / "x.db"))
        tq = TaskQueue(str(tmp_path / "x.db"))
        tid = tq.enqueue("/p", "x")
        n = stamp_origin_by_token(
            db_path=str(tmp_path / "x.db"),
            dispatch_token="", reply_to="alias@example.com",
        )
        assert n == 0
        assert tq.get(tid)["origin_from"] is None

    def test_no_match_is_silent_noop(self, tmp_path):
        """LLM only answered in plain text and never enqueued — fine."""
        from src.reply_routing_fixup import stamp_origin_by_token
        ChatDB(str(tmp_path / "x.db"))
        n = stamp_origin_by_token(
            db_path=str(tmp_path / "x.db"),
            dispatch_token="tok-no-match", reply_to="alias@example.com",
        )
        assert n == 0
