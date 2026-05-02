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


class TestMcpDispatchIgnoresOriginArgs:
    """Security: chat_enqueue_task is exposed to every MCP client/agent.
    If origin_from / origin_message_id were trusted from MCP arguments,
    any caller could hijack a task's reply address (relay treats
    origin_message_id-set tasks as email-origin and addresses replies
    to origin_from). The dispatcher must drop these fields; the
    deterministic email-router fixup stamps them from the inbound
    message instead."""

    def test_dispatch_ignores_origin_args_from_mcp(self, tmp_path, mocker, monkeypatch):
        import asyncio
        from chat.dispatch import dispatch
        from src.task_queue import TaskQueue
        from src.worker_manager import WorkerManager
        from src.reset_control import TokenStore
        monkeypatch.setenv("CLAUDE_CWD", str(tmp_path))
        (tmp_path / "p").mkdir()
        ChatDB(str(tmp_path / "x.db"))
        db = ChatDB(str(tmp_path / "x.db"))
        queue = TaskQueue(str(tmp_path / "x.db"))
        mocker.patch("src.worker_manager.is_alive", return_value=True)
        mocker.patch(
            "src.worker_manager._find_external_worker_pid", return_value=None,
        )
        manager = WorkerManager(
            db_path=str(tmp_path / "x.db"), project_root=str(tmp_path),
        )
        tokens = TokenStore()
        proc = mocker.MagicMock(pid=4242)
        proc.poll.return_value = None
        mocker.patch("src.worker_manager.subprocess.Popen", return_value=proc)
        result = asyncio.run(dispatch(
            db, queue, manager, tokens,
            "chat_enqueue_task",
            {
                "project": "p", "body": "do the thing",
                # Attacker-supplied; must NOT land on the row.
                "origin_from": "attacker@example.com",
                "origin_message_id": "<spoofed@example.com>",
                "origin_subject": "spoofed",
                "origin_content_type": "application/json",
            },
        ))
        assert "task_id" in result
        row = queue.get(result["task_id"])
        assert row["origin_from"] is None
        assert row["origin_message_id"] is None
        assert row["origin_subject"] is None


class TestPostExecuteOriginFromFixup:
    """Safety net: tasks created via the LLM-router MCP path land with
    origin_*=NULL (the dispatcher refuses to trust LLM-supplied values).
    The fixup must stamp origin_from / origin_message_id / origin_subject
    from the trusted inbound message before relay_outbound_messages
    fires — without origin_message_id, ``chat_relay._should_relay`` drops
    the [Update] entirely instead of sending it."""

    def test_stamps_unstamped_task_in_dispatch_window(self, tmp_path):
        from src.reply_routing_fixup import stamp_origin_for_window
        ChatDB(str(tmp_path / "x.db"))
        tq = TaskQueue(str(tmp_path / "x.db"))
        cdb = ChatDB(str(tmp_path / "x.db"))
        proj = str(tmp_path / "p")
        (tmp_path / "p").mkdir()
        from datetime import datetime, timezone
        started = datetime.now(timezone.utc).isoformat()
        tid = tq.enqueue(proj, "do work")
        n = stamp_origin_for_window(
            db_path=str(tmp_path / "x.db"),
            allowed_base=str(tmp_path),
            reply_to="alias@example.com",
            started_at_iso=started,
            origin_message_id="<m-1@example.com>",
            origin_subject="Re: do work",
        )
        assert n == 1
        row = tq.get(tid)
        assert row["origin_from"] == "alias@example.com"
        # origin_message_id is required by chat_relay._should_relay —
        # without it the [Update] is silently dropped.
        assert row["origin_message_id"] == "<m-1@example.com>"
        assert row["origin_subject"] == "Re: do work"

    def test_does_not_overwrite_existing_origin_from(self, tmp_path):
        from src.reply_routing_fixup import stamp_origin_from_for_window
        ChatDB(str(tmp_path / "x.db"))
        tq = TaskQueue(str(tmp_path / "x.db"))
        proj = str(tmp_path / "p")
        (tmp_path / "p").mkdir()
        from datetime import datetime, timezone
        started = datetime.now(timezone.utc).isoformat()
        tid = tq.enqueue(proj, "x", origin_from="real@example.com")
        n = stamp_origin_from_for_window(
            db_path=str(tmp_path / "x.db"),
            allowed_base=str(tmp_path),
            reply_to="should-not-win@example.com",
            started_at_iso=started,
        )
        assert n == 0
        assert tq.get(tid)["origin_from"] == "real@example.com"

    def test_skips_tasks_outside_allowed_base(self, tmp_path):
        from src.reply_routing_fixup import stamp_origin_from_for_window
        ChatDB(str(tmp_path / "x.db"))
        tq = TaskQueue(str(tmp_path / "x.db"))
        from datetime import datetime, timezone
        started = datetime.now(timezone.utc).isoformat()
        # Task is in a DIFFERENT universe's project tree.
        tid = tq.enqueue("/some/other/path", "x")
        n = stamp_origin_from_for_window(
            db_path=str(tmp_path / "x.db"),
            allowed_base=str(tmp_path),
            reply_to="alias@example.com",
            started_at_iso=started,
        )
        assert n == 0
        assert tq.get(tid)["origin_from"] is None

    def test_skips_tasks_created_before_window(self, tmp_path):
        from src.reply_routing_fixup import stamp_origin_from_for_window
        import time
        ChatDB(str(tmp_path / "x.db"))
        tq = TaskQueue(str(tmp_path / "x.db"))
        proj = str(tmp_path / "p")
        (tmp_path / "p").mkdir()
        tid = tq.enqueue(proj, "old task")  # before the window
        time.sleep(0.01)
        from datetime import datetime, timezone
        started = datetime.now(timezone.utc).isoformat()
        n = stamp_origin_from_for_window(
            db_path=str(tmp_path / "x.db"),
            allowed_base=str(tmp_path),
            reply_to="alias@example.com",
            started_at_iso=started,
        )
        assert n == 0
        assert tq.get(tid)["origin_from"] is None

    def test_empty_allowed_base_is_noop(self, tmp_path):
        """Without an allowed_base we can't safely scope the stamp."""
        from src.reply_routing_fixup import stamp_origin_from_for_window
        ChatDB(str(tmp_path / "x.db"))
        n = stamp_origin_from_for_window(
            db_path=str(tmp_path / "x.db"),
            allowed_base="",
            reply_to="alias@example.com",
            started_at_iso="2026-05-02T00:00:00+00:00",
        )
        assert n == 0

    def test_underscore_in_allowed_base_does_not_overmatch(self, tmp_path):
        """Codex repro: SQLite LIKE treats ``_`` as single-char wildcard,
        so ``allowed_base=/tmp/foo_bar`` would otherwise match a task at
        ``/tmp/fooxbar/proj`` and stamp it across universes."""
        from src.reply_routing_fixup import stamp_origin_from_for_window
        ChatDB(str(tmp_path / "x.db"))
        tq = TaskQueue(str(tmp_path / "x.db"))
        from datetime import datetime, timezone
        started = datetime.now(timezone.utc).isoformat()
        # Underscore in the universe's allowed_base.
        base = str(tmp_path / "foo_bar")
        (tmp_path / "foo_bar").mkdir()
        # Different-universe task path that would match /tmp/foo_bar/% via
        # LIKE if the underscore weren't escaped.
        other_path = str(tmp_path / "fooxbar" / "proj")
        (tmp_path / "fooxbar" / "proj").mkdir(parents=True)
        tid = tq.enqueue(other_path, "x")
        n = stamp_origin_from_for_window(
            db_path=str(tmp_path / "x.db"),
            allowed_base=base,
            reply_to="alias@example.com",
            started_at_iso=started,
        )
        assert n == 0
        assert tq.get(tid)["origin_from"] is None


class TestRunRouterWithFixup:
    """The orchestration wrapper main.process_email uses — wraps the
    LLM-router call with timestamp-window + post-execute stamp pass."""

    def test_runs_executor_and_stamps_orphan_tasks(self, tmp_path):
        from src.reply_routing_fixup import run_router_with_fixup
        ChatDB(str(tmp_path / "x.db"))
        tq = TaskQueue(str(tmp_path / "x.db"))
        proj = str(tmp_path / "p")
        (tmp_path / "p").mkdir()

        def _execute():
            # Simulate the LLM router enqueuing a task without origin_from.
            tq.enqueue(proj, "do work")
            return "executor-output"

        result = run_router_with_fixup(
            _execute,
            db_path=str(tmp_path / "x.db"),
            allowed_base=str(tmp_path),
            reply_to="alias@example.com",
        )
        assert result == "executor-output"
        # Find the task and confirm it was stamped.
        rows = [r["origin_from"] for r in tq._conn.execute(  # noqa: SLF001
            "SELECT origin_from FROM tasks")]
        assert rows == ["alias@example.com"]

    def test_stamp_failure_does_not_break_dispatch(self, tmp_path, mocker):
        """A buggy fixup must never poison the executor's output."""
        from src.reply_routing_fixup import run_router_with_fixup
        mocker.patch(
            "src.reply_routing_fixup.stamp_origin_from_for_window",
            side_effect=RuntimeError("disk full"),
        )
        result = run_router_with_fixup(
            lambda: "still-works",
            db_path=str(tmp_path / "x.db"),
            allowed_base=str(tmp_path),
            reply_to="alias@example.com",
        )
        assert result == "still-works"

    def test_skips_stamp_when_reply_to_missing(self, tmp_path, mocker):
        from src.reply_routing_fixup import run_router_with_fixup
        stamp = mocker.patch("src.reply_routing_fixup.stamp_origin_from_for_window")
        run_router_with_fixup(
            lambda: "out",
            db_path=str(tmp_path / "x.db"),
            allowed_base=str(tmp_path),
            reply_to="",
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
