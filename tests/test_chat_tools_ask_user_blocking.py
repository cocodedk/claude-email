"""Tests for the MCP tool handler functions (chat/tools.py).

Covers: ask_user — basic blocking behaviour, timeout, message creation,
last_seen heartbeat, and task_id propagation.
"""
import asyncio
import pytest
from chat.tools import ask_user
from tests._chat_tools_helpers import db  # noqa: F401


# ── ask_user ──────────────────────────────────────────────────

class TestAskUser:
    @pytest.mark.asyncio
    async def test_blocks_then_returns_reply(self, db):
        db.register_agent("bot", "/p")

        async def delayed_reply():
            """Wait briefly, find the pending ask, and reply to it."""
            await asyncio.sleep(0.05)
            pending = db.get_pending_messages_for("user")
            ask_msg = [m for m in pending if m["type"] == "ask"][0]
            db.insert_message(
                "user", "bot", "yes, go ahead", "reply",
                in_reply_to=ask_msg["id"],
            )

        task = asyncio.create_task(delayed_reply())
        result = await ask_user(db, "bot", "May I proceed?", poll_interval=0.02)
        await task
        assert result == {"reply": "yes, go ahead"}

    @pytest.mark.asyncio
    async def test_timeout_returns_error(self, db):
        db.register_agent("bot", "/p")
        result = await ask_user(
            db, "bot", "question?", poll_interval=0.01, timeout=0.03,
        )
        assert "error" in result
        assert "No reply" in result["error"]

    @pytest.mark.asyncio
    async def test_creates_ask_message(self, db):
        db.register_agent("bot", "/p")

        async def quick_reply():
            await asyncio.sleep(0.02)
            pending = db.get_pending_messages_for("user")
            ask_msg = [m for m in pending if m["type"] == "ask"][0]
            db.insert_message(
                "user", "bot", "ok", "reply", in_reply_to=ask_msg["id"],
            )

        task = asyncio.create_task(quick_reply())
        await ask_user(db, "bot", "question?", poll_interval=0.01)
        await task

        # The ask message should exist in the DB
        msgs = db._conn.execute(
            "SELECT * FROM messages WHERE type='ask' AND from_name='bot'"
        ).fetchall()
        assert len(msgs) == 1

    @pytest.mark.asyncio
    async def test_touches_agent_last_seen(self, db):
        db.register_agent("bot", "/p")
        first = db.get_agent("bot")["last_seen_at"]
        import time
        time.sleep(0.01)
        # Timeout fast; we only care that ask touched last_seen before blocking.
        await ask_user(
            db, "bot", "question?", poll_interval=0.005, timeout=0.02,
        )
        second = db.get_agent("bot")["last_seen_at"]
        assert second > first

    @pytest.mark.asyncio
    async def test_ask_with_task_id_stores_task_id(self, db):
        db.register_agent("bot", "/p")
        task_id = db._conn.execute(
            "INSERT INTO tasks (project_path, body, status, created_at) VALUES (?, ?, ?, ?)",
            ("/p", "work", "running", "2026-01-01T00:00:00"),
        ).lastrowid
        db._conn.commit()

        async def quick_reply():
            await asyncio.sleep(0.02)
            pending = db.get_pending_messages_for("user")
            ask_msg = [m for m in pending if m["type"] == "ask"][0]
            db.insert_message("user", "bot", "ok", "reply", in_reply_to=ask_msg["id"])

        task = asyncio.create_task(quick_reply())
        await ask_user(db, "bot", "question?", poll_interval=0.01, task_id=task_id)
        await task

        row = db._conn.execute(
            "SELECT task_id FROM messages WHERE type='ask' AND from_name='bot'"
        ).fetchone()
        assert row["task_id"] == task_id
