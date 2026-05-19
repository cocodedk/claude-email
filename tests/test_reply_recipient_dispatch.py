"""MCP-dispatch reply-routing tests: the dispatcher must drop attacker-
supplied ``origin_*`` fields so chat_enqueue_task can't be used to
hijack a task's reply address.

Split out of ``tests/test_reply_recipient.py``.
"""
import pytest

from src.chat_db import ChatDB
from src.task_queue import TaskQueue


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
