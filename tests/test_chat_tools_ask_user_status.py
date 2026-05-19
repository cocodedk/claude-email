"""Tests for the MCP tool handler functions (chat/tools.py).

Covers: ask_user status-envelope side effects — waiting-on-peer emission,
dedup clearing on resolve/timeout, ordering vs. ask, and the no-task_id
silent-path.
"""
import asyncio
import pytest
from chat.tools import ask_user
from tests._chat_tools_helpers import db, _running_json_task  # noqa: F401


class TestAskUserStatusEnvelope:
    @pytest.mark.asyncio
    async def test_ask_with_task_id_emits_waiting_on_peer_status(self, db):
        """When chat_ask fires on a task-linked call, the server emits a
        kind=status envelope (data.status=waiting-on-peer) so the client
        can light up a 'waiting for input' glyph on the pending task."""
        import json
        db.register_agent("bot", "/p")
        task_id = _running_json_task(db)

        async def quick_reply():
            await asyncio.sleep(0.02)
            pending = db.get_pending_messages_for("user")
            ask_msg = [m for m in pending if m["type"] == "ask"][0]
            db.insert_message("user", "bot", "ok", "reply", in_reply_to=ask_msg["id"])

        task = asyncio.create_task(quick_reply())
        await ask_user(db, "bot", "question?", poll_interval=0.01, task_id=task_id)
        await task

        status_rows = db._conn.execute(
            "SELECT body FROM messages WHERE content_type='application/json' "
            "AND task_id=? ORDER BY id", (task_id,),
        ).fetchall()
        assert len(status_rows) == 1
        env = json.loads(status_rows[0]["body"])
        assert env["kind"] == "status"
        assert env["data"]["status"] == "waiting-on-peer"
        assert env["data"]["reason"] == "awaiting user answer"

    @pytest.mark.asyncio
    async def test_ask_timeout_also_clears_status_dedup(self, db):
        """Timeout is a state-end too — without clearing here, a retried
        chat_ask after the agent gives up would silently dedupe its own
        waiting-on-peer envelope and the frontend wouldn't relight."""
        db.register_agent("bot", "/p")
        task_id = _running_json_task(db)
        result = await ask_user(
            db, "bot", "q?", poll_interval=0.005, timeout=0.02, task_id=task_id,
        )
        assert "error" in result
        row = db._conn.execute(
            "SELECT last_sent_status FROM tasks WHERE id=?", (task_id,)
        ).fetchone()
        assert row["last_sent_status"] is None

    @pytest.mark.asyncio
    async def test_ask_clears_status_dedup_so_repeat_asks_re_emit(self, db):
        """After a chat_ask resolves, last_sent_status must clear — a
        long-running task that asks twice should fire waiting-on-peer
        twice, otherwise the frontend's waiting glyph goes stale on the
        second wait."""
        db.register_agent("bot", "/p")
        task_id = _running_json_task(db)

        async def reply_once():
            await asyncio.sleep(0.02)
            asks = [m for m in db.get_pending_messages_for("user") if m["type"] == "ask"]
            db.insert_message("user", "bot", "ok", "reply", in_reply_to=asks[-1]["id"])

        for _ in range(2):
            t = asyncio.create_task(reply_once())
            await ask_user(db, "bot", "q?", poll_interval=0.005, task_id=task_id)
            await t

        notify_count = db._conn.execute(
            "SELECT COUNT(*) AS n FROM messages "
            "WHERE type='notify' AND content_type='application/json' AND task_id=?",
            (task_id,),
        ).fetchone()["n"]
        assert notify_count == 2

    @pytest.mark.asyncio
    async def test_ask_inserts_status_before_ask_so_reply_threads_to_ask(self, db):
        """The ask message must have a higher id than the waiting-on-peer
        status notify. Mail clients thread by In-Reply-To and users reply
        to the latest visible message — if the status notify came last,
        the reply would target the notify and classify_reply would skip
        the ask route, leaving the blocking chat_ask to time out."""
        db.register_agent("bot", "/p")
        task_id = _running_json_task(db)

        async def quick_reply():
            await asyncio.sleep(0.02)
            pending = db.get_pending_messages_for("user")
            ask_msg = [m for m in pending if m["type"] == "ask"][0]
            db.insert_message("user", "bot", "ok", "reply", in_reply_to=ask_msg["id"])

        task = asyncio.create_task(quick_reply())
        await ask_user(db, "bot", "q?", poll_interval=0.01, task_id=task_id)
        await task

        rows = db._conn.execute(
            "SELECT id, type FROM messages WHERE to_name='user' "
            "AND task_id=? ORDER BY id",
            (task_id,),
        ).fetchall()
        assert [r["type"] for r in rows] == ["notify", "ask"]
        assert rows[0]["id"] < rows[1]["id"]

    @pytest.mark.asyncio
    async def test_ask_without_task_id_no_status_emitted(self, db):
        """Un-threaded chat_ask (no task context) does not emit a status
        envelope — there's no task to attach it to."""
        db.register_agent("bot", "/p")

        async def quick_reply():
            await asyncio.sleep(0.02)
            pending = db.get_pending_messages_for("user")
            ask_msg = [m for m in pending if m["type"] == "ask"][0]
            db.insert_message("user", "bot", "ok", "reply", in_reply_to=ask_msg["id"])

        task = asyncio.create_task(quick_reply())
        await ask_user(db, "bot", "question?", poll_interval=0.01)
        await task

        status_count = db._conn.execute(
            "SELECT COUNT(*) c FROM messages WHERE content_type='application/json'"
        ).fetchone()["c"]
        assert status_count == 0
