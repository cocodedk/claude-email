"""Dispatch-token security tests: the token is an effective bearer
credential, so it must be redacted from public read paths and trimmed
of whitespace before persistence.

Split out of ``tests/test_reply_recipient.py``.
"""
import pytest

from src.chat_db import ChatDB
from src.task_queue import TaskQueue


class TestDispatchTokenSecurity:
    """Codex flagged the dispatch_token as an effective bearer token:
    knowing it lets a caller inject their own task into the email-router's
    correlation window so the fixup stamps the inbound sender onto the
    attacker's row. Tests pin two defenses:

      1. The token is redacted from every public task-row read path
         (chat_queue_status / chat_where_am_i / direct ``get``) so a
         polling MCP client can't observe it mid-dispatch.
      2. The dispatcher strips whitespace from MCP-supplied tokens so
         the LLM's ``echo "$VAR"`` newline doesn't break the fixup's
         exact-match lookup.
    """

    def test_get_redacts_dispatch_token(self, tmp_path):
        ChatDB(str(tmp_path / "x.db"))
        tq = TaskQueue(str(tmp_path / "x.db"))
        tid = tq.enqueue("/p", "x", dispatch_token="tok-secret")
        row = tq.get(tid)
        assert "dispatch_token" not in row

    def test_get_running_redacts(self, tmp_path):
        ChatDB(str(tmp_path / "x.db"))
        tq = TaskQueue(str(tmp_path / "x.db"))
        tq.enqueue("/p", "x", dispatch_token="tok-secret")
        tq.claim_next("/p")
        row = tq.get_running("/p")
        assert "dispatch_token" not in row

    def test_list_pending_redacts(self, tmp_path):
        ChatDB(str(tmp_path / "x.db"))
        tq = TaskQueue(str(tmp_path / "x.db"))
        tq.enqueue("/p", "x", dispatch_token="tok-secret")
        for r in tq.list_pending("/p"):
            assert "dispatch_token" not in r

    def test_claim_next_redacts(self, tmp_path):
        """The worker reads ``claim_next``'s return; it never needs the
        token, and leaking it via worker-side logs would also expose it."""
        ChatDB(str(tmp_path / "x.db"))
        tq = TaskQueue(str(tmp_path / "x.db"))
        tq.enqueue("/p", "x", dispatch_token="tok-secret")
        claimed = tq.claim_next("/p")
        assert "dispatch_token" not in claimed

    def test_dispatcher_strips_token_whitespace(self, tmp_path, mocker, monkeypatch):
        """LLM's ``echo "$CLAUDE_EMAIL_DISPATCH_TOKEN"`` appends a
        newline. Persisting ``<uuid>\\n`` would break the fixup's exact
        match against the no-newline UUID minted in main.py."""
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
                "project": "p", "body": "x",
                "dispatch_token": "  tok-from-echo\n",
            },
        ))
        # Read the raw row directly to bypass redaction — verify the
        # stored token is the stripped value.
        raw = queue._conn.execute(  # noqa: SLF001
            "SELECT dispatch_token FROM tasks WHERE id=?", (result["task_id"],),
        ).fetchone()
        assert raw["dispatch_token"] == "tok-from-echo"
