"""Coverage for chat/dispatch.py: _heartbeat and dispatch-level touch hook."""
import asyncio

import pytest

from chat.dispatch import _heartbeat, dispatch
from src.chat_db import ChatDB


class TestHeartbeat:
    """Every MCP tool invocation should refresh last_seen_at for its
    caller. Before this hook existed, only chat_check_messages did it —
    so an agent that only sent (never polled) looked stale to the
    dashboard. Silent no-op when _caller is missing / not yet registered."""

    @pytest.fixture
    def db(self, tmp_path):
        return ChatDB(str(tmp_path / "test.db"))

    def test_registered_caller_is_touched(self, db):
        db.register_agent("bot", "/p")
        before = db.get_agent("bot")["last_seen_at"]
        # Force a measurable gap so the comparison is meaningful.
        db._conn.execute(
            "UPDATE agents SET last_seen_at='1970-01-01T00:00:00+00:00' WHERE name='bot'"
        )
        db._conn.commit()
        _heartbeat(db, {"_caller": "bot"})
        after = db.get_agent("bot")["last_seen_at"]
        assert after > "1970-01-01T00:00:00+00:00"
        assert after != before or after > before

    def test_missing_caller_noop(self, db):
        # Nothing to register, no exception
        _heartbeat(db, {})
        _heartbeat(db, {"_caller": None})
        _heartbeat(db, {"_caller": "   "})

    def test_unknown_caller_noop(self, db):
        # Not yet registered — touch silently does nothing
        _heartbeat(db, {"_caller": "nobody"})
        assert db.get_agent("nobody") is None

    def test_broken_db_does_not_raise(self):
        """Telemetry must never block a real tool call."""
        class _Broken:
            def touch_agent(self, *_a, **_k):
                raise RuntimeError("disk full")
        _heartbeat(_Broken(), {"_caller": "anyone"})  # no raise

    def test_dispatch_touches_before_routing(self, db, tmp_path, monkeypatch):
        """A tool that doesn't itself touch (e.g. chat_notify) still gets
        last_seen_at refreshed because dispatch calls _heartbeat up-front."""
        from src.task_queue import TaskQueue
        from src.worker_manager import WorkerManager
        from src.reset_control import TokenStore
        db.register_agent("bot", "/p")
        db._conn.execute(
            "UPDATE agents SET last_seen_at='1970-01-01T00:00:00+00:00' WHERE name='bot'"
        )
        db._conn.commit()
        queue = TaskQueue(str(tmp_path / "q.db"))
        manager = WorkerManager(
            db_path=str(tmp_path / "q.db"),
            project_root=str(tmp_path),
        )
        tokens = TokenStore()
        result = asyncio.run(dispatch(
            db, queue, manager, tokens,
            "chat_notify", {"_caller": "bot", "message": "ping"},
        ))
        assert result == {"status": "sent"}
        assert db.get_agent("bot")["last_seen_at"] > "1970-01-01T00:00:00+00:00"

    def test_dispatch_chat_ask_forwards_suggested_replies(self, db, tmp_path):
        """chat_ask with suggested_replies must reach ask_user so the
        kind=question envelope is built for JSON-origin tasks (C2)."""
        import json
        from src.task_queue import TaskQueue
        from src.worker_manager import WorkerManager
        from src.reset_control import TokenStore
        db.register_agent("bot", "/p")
        queue = TaskQueue(db.path)
        task_id = queue.enqueue(
            "/p", "work", origin_content_type="application/json",
        )
        queue.claim_next("/p")
        manager = WorkerManager(db_path=db.path, project_root=str(tmp_path))
        tokens = TokenStore()

        async def driver():
            async def reply_after_delay():
                await asyncio.sleep(0.02)
                pending = db.get_pending_messages_for("user")
                ask_msg = [m for m in pending if m["type"] == "ask"][0]
                db.insert_message(
                    "user", "bot", "yes", "reply", in_reply_to=ask_msg["id"],
                )
            replier = asyncio.create_task(reply_after_delay())
            # Patch the ask_user poll interval via the timeout knob — short
            # poll keeps the test fast.
            from chat import tools
            original = tools.ask_user
            async def fast_ask(*args, **kwargs):
                kwargs["poll_interval"] = 0.01
                return await original(*args, **kwargs)
            tools.ask_user = fast_ask
            try:
                await dispatch(
                    db, queue, manager, tokens,
                    "chat_ask",
                    {
                        "_caller": "bot", "message": "Commit?",
                        "task_id": task_id,
                        "suggested_replies": ["yes", "no", "edit first"],
                    },
                )
            finally:
                tools.ask_user = original
            await replier

        asyncio.run(driver())
        ask_row = db._conn.execute(
            "SELECT body, content_type FROM messages "
            "WHERE type='ask' AND from_name='bot'"
        ).fetchone()
        assert ask_row["content_type"] == "application/json"
        env = json.loads(ask_row["body"])
        assert env["kind"] == "question"
        assert env["meta"]["suggested_replies"] == ["yes", "no", "edit first"]

    def test_dispatch_chat_notify_forwards_progress(self, db, tmp_path):
        """chat_notify with progress arg must reach notify_user so the
        envelope wrap kicks in for JSON-origin tasks (B5)."""
        import json
        from src.task_queue import TaskQueue
        from src.worker_manager import WorkerManager
        from src.reset_control import TokenStore
        db.register_agent("bot", "/p")
        queue = TaskQueue(db.path)
        task_id = queue.enqueue(
            "/p", "work", origin_content_type="application/json",
        )
        queue.claim_next("/p")
        manager = WorkerManager(
            db_path=db.path, project_root=str(tmp_path),
        )
        tokens = TokenStore()
        asyncio.run(dispatch(
            db, queue, manager, tokens,
            "chat_notify",
            {
                "_caller": "bot", "message": "Running tests",
                "task_id": task_id,
                "progress": {"current": 3, "total": 7, "label": "passed"},
            },
        ))
        msg = db.get_pending_messages_for("user")[0]
        assert msg["content_type"] == "application/json"
        env = json.loads(msg["body"])
        assert env["kind"] == "progress"
        assert env["meta"]["progress"] == {
            "current": 3, "total": 7, "label": "passed",
        }
