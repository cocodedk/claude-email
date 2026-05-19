"""Tests for the ``run_router_with_fixup`` orchestration wrapper and the
LLM-router prompt's refusal to leak the inbound sender.

Split out of ``tests/test_reply_recipient.py``.
"""
import pytest

from src.chat_db import ChatDB
from src.task_queue import TaskQueue


class TestRunRouterWithFixup:
    """The orchestration wrapper main.process_email uses — runs the
    LLM-router call, then stamps every task carrying the dispatch token."""

    def test_runs_executor_and_stamps_token_tasks(self, tmp_path):
        from src.reply_routing_fixup import run_router_with_fixup
        ChatDB(str(tmp_path / "x.db"))
        tq = TaskQueue(str(tmp_path / "x.db"))
        proj = str(tmp_path / "p")
        (tmp_path / "p").mkdir()

        def _execute():
            tq.enqueue(proj, "do work", dispatch_token="tok-abc")
            return "executor-output"

        result = run_router_with_fixup(
            _execute,
            db_path=str(tmp_path / "x.db"),
            dispatch_token="tok-abc",
            reply_to="alias@example.com",
            origin_message_id="<m-1@example.com>",
        )
        assert result == "executor-output"
        rows = [r["origin_from"] for r in tq._conn.execute(  # noqa: SLF001
            "SELECT origin_from FROM tasks")]
        assert rows == ["alias@example.com"]

    def test_stamp_failure_does_not_break_dispatch(self, tmp_path, mocker):
        from src.reply_routing_fixup import run_router_with_fixup
        mocker.patch(
            "src.reply_routing_fixup.stamp_origin_by_token",
            side_effect=RuntimeError("disk full"),
        )
        result = run_router_with_fixup(
            lambda: "still-works",
            db_path=str(tmp_path / "x.db"),
            dispatch_token="tok-1",
            reply_to="alias@example.com",
        )
        assert result == "still-works"

    def test_skips_stamp_when_token_missing(self, tmp_path, mocker):
        from src.reply_routing_fixup import run_router_with_fixup
        stamp = mocker.patch("src.reply_routing_fixup.stamp_origin_by_token")
        run_router_with_fixup(
            lambda: "out",
            db_path=str(tmp_path / "x.db"),
            dispatch_token="", reply_to="alias@example.com",
        )
        stamp.assert_not_called()


class TestLlmRouterPromptDoesNotLeakSender:
    """Codex caught: trusting LLM-supplied origin_from is a routing-
    hijack vector. The prompt must not instruct the LLM to pass it,
    and must not embed the sender into the prompt at all (the fixup
    handles routing deterministically)."""

    def test_build_prompt_ignores_reply_to(self):
        from src.llm_router import build_email_router_prompt
        out_with = build_email_router_prompt(reply_to="alias@example.com")
        out_without = build_email_router_prompt(reply_to="")
        # The prompt is sender-agnostic — same text either way.
        assert out_with == out_without
        assert "alias@example.com" not in out_with
        # And no placeholder leaking through.
        assert "{reply_to}" not in out_with
