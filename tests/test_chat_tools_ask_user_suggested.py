"""Tests for the MCP tool handler functions (chat/tools.py).

Covers: ask_user suggested_replies handling — JSON envelope construction,
plain-origin fallback, missing replies, filtering/dedup/trim/cap, and
all-invalid fallback.
"""
import asyncio
import pytest
from chat.tools import ask_user
from tests._chat_tools_helpers import db, _running_json_task  # noqa: F401


class TestAskUserSuggestedReplies:
    """meta.suggested_replies on kind=question envelopes — wire format
    locked with agent-Claude-Email-App on 2026-05-03 (C2)."""

    @pytest.mark.asyncio
    async def test_json_task_with_replies_builds_question_envelope(self, db):
        import json
        db.register_agent("bot", "/p")
        task_id = _running_json_task(db)

        async def reply():
            await asyncio.sleep(0.02)
            pending = db.get_pending_messages_for("user")
            ask_msg = [m for m in pending if m["type"] == "ask"][0]
            db.insert_message("user", "bot", "yes", "reply", in_reply_to=ask_msg["id"])

        task = asyncio.create_task(reply())
        await ask_user(
            db, "bot", "Commit and push?",
            poll_interval=0.01, task_id=task_id,
            suggested_replies=["yes", "no", "edit first"],
        )
        await task

        ask_row = db._conn.execute(
            "SELECT body, content_type FROM messages "
            "WHERE type='ask' AND from_name='bot'"
        ).fetchone()
        assert ask_row["content_type"] == "application/json"
        env = json.loads(ask_row["body"])
        assert env["kind"] == "question"
        assert env["body"] == "Commit and push?"
        assert env["meta"]["suggested_replies"] == ["yes", "no", "edit first"]

    @pytest.mark.asyncio
    async def test_plain_origin_task_keeps_plain_text(self, db):
        """Plain-origin task (no app) doesn't get JSON envelopes — same
        branch-on-origin pattern as progress + task_notifier."""
        db.register_agent("bot", "/p")
        task_id = db._conn.execute(
            "INSERT INTO tasks (project_path, body, status, created_at) VALUES (?, ?, ?, ?)",
            ("/p", "work", "running", "2026-01-01T00:00:00"),
        ).lastrowid
        db._conn.commit()

        async def reply():
            await asyncio.sleep(0.02)
            pending = db.get_pending_messages_for("user")
            ask_msg = [m for m in pending if m["type"] == "ask"][0]
            db.insert_message("user", "bot", "ok", "reply", in_reply_to=ask_msg["id"])

        task = asyncio.create_task(reply())
        await ask_user(
            db, "bot", "Commit?",
            poll_interval=0.01, task_id=task_id,
            suggested_replies=["yes", "no"],
        )
        await task

        ask_row = db._conn.execute(
            "SELECT body, content_type FROM messages "
            "WHERE type='ask' AND from_name='bot'"
        ).fetchone()
        assert (ask_row["content_type"] or "") == ""
        assert ask_row["body"] == "Commit?"

    @pytest.mark.asyncio
    async def test_no_replies_keeps_plain_text(self, db):
        """Backward compat: omitted suggested_replies = existing plain text."""
        db.register_agent("bot", "/p")
        task_id = _running_json_task(db)

        async def reply():
            await asyncio.sleep(0.02)
            pending = db.get_pending_messages_for("user")
            ask_msg = [m for m in pending if m["type"] == "ask"][0]
            db.insert_message("user", "bot", "ok", "reply", in_reply_to=ask_msg["id"])

        task = asyncio.create_task(reply())
        await ask_user(db, "bot", "Q?", poll_interval=0.01, task_id=task_id)
        await task

        ask_row = db._conn.execute(
            "SELECT body, content_type FROM messages "
            "WHERE type='ask' AND from_name='bot'"
        ).fetchone()
        assert (ask_row["content_type"] or "") == ""
        assert ask_row["body"] == "Q?"

    @pytest.mark.asyncio
    async def test_replies_filtered_dedup_trim_cap(self, db):
        import json
        db.register_agent("bot", "/p")
        task_id = _running_json_task(db)

        async def reply():
            await asyncio.sleep(0.02)
            pending = db.get_pending_messages_for("user")
            ask_msg = [m for m in pending if m["type"] == "ask"][0]
            db.insert_message("user", "bot", "ok", "reply", in_reply_to=ask_msg["id"])

        task = asyncio.create_task(reply())
        await ask_user(
            db, "bot", "Q?",
            poll_interval=0.01, task_id=task_id,
            suggested_replies=[
                "  yes  ",       # trim → "yes"
                "yes",           # dedup with above
                "",              # drop: empty after trim
                "   ",           # drop: whitespace-only
                None,            # drop: non-str
                "no",            # keep
                "x" * 31,        # drop: too long
                "maybe",         # keep
                "edit first",    # keep
                "later",         # cap at 4 → drop
            ],
        )
        await task

        env = json.loads(db._conn.execute(
            "SELECT body FROM messages WHERE type='ask' AND from_name='bot'",
        ).fetchone()["body"])
        assert env["meta"]["suggested_replies"] == [
            "yes", "no", "maybe", "edit first",
        ]

    @pytest.mark.asyncio
    async def test_all_replies_invalid_falls_back_to_plain_text(self, db):
        db.register_agent("bot", "/p")
        task_id = _running_json_task(db)

        async def reply():
            await asyncio.sleep(0.02)
            pending = db.get_pending_messages_for("user")
            ask_msg = [m for m in pending if m["type"] == "ask"][0]
            db.insert_message("user", "bot", "ok", "reply", in_reply_to=ask_msg["id"])

        task = asyncio.create_task(reply())
        await ask_user(
            db, "bot", "Q?",
            poll_interval=0.01, task_id=task_id,
            suggested_replies=["", "   ", None, "x" * 31],
        )
        await task

        ask_row = db._conn.execute(
            "SELECT body, content_type FROM messages "
            "WHERE type='ask' AND from_name='bot'"
        ).fetchone()
        assert (ask_row["content_type"] or "") == ""
        assert ask_row["body"] == "Q?"
