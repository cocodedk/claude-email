"""Coverage for chat/dispatch.py helpers: _parse_task_id, _parse_bool, _heartbeat."""
import asyncio

import pytest

from chat.dispatch import _heartbeat, _parse_bool, _parse_task_id, dispatch
from src.chat_db import ChatDB


class TestParseTaskId:
    def test_missing_returns_none(self):
        assert _parse_task_id({}) is None

    def test_explicit_none_returns_none(self):
        assert _parse_task_id({"task_id": None}) is None

    def test_numeric_string_parses(self):
        assert _parse_task_id({"task_id": "42"}) == 42

    def test_int_passes_through(self):
        assert _parse_task_id({"task_id": 7}) == 7

    def test_non_numeric_string_returns_none(self):
        """A non-numeric string must not raise — dispatch drops the task_id
        so the tool call succeeds without threading."""
        assert _parse_task_id({"task_id": "not-a-number"}) is None

    def test_unsupported_type_returns_none(self):
        """Lists, dicts, etc. should coerce to None (TypeError path)."""
        assert _parse_task_id({"task_id": ["x"]}) is None


class TestParseBool:
    def test_true_passthrough(self):
        assert _parse_bool(True) is True

    def test_false_passthrough(self):
        assert _parse_bool(False) is False

    def test_truthy_strings(self):
        for v in ("true", "True", "1", "yes", "Y", "on"):
            assert _parse_bool(v) is True, v

    def test_falsy_strings(self):
        """Includes 'false' — bool('false') would be True (non-empty),
        which is why we can't just use bool()."""
        for v in ("false", "False", "0", "no", "N", "off", ""):
            assert _parse_bool(v) is False, v

    def test_int_truthy(self):
        assert _parse_bool(1) is True
        assert _parse_bool(42) is True

    def test_int_falsy(self):
        assert _parse_bool(0) is False

    def test_float_truthy_and_falsy(self):
        assert _parse_bool(1.5) is True
        assert _parse_bool(0.0) is False

    def test_unknown_string_returns_default(self):
        assert _parse_bool("maybe") is False
        assert _parse_bool("maybe", default=True) is True

    def test_other_type_returns_default(self):
        assert _parse_bool(None) is False
        assert _parse_bool([1]) is False
        assert _parse_bool({}, default=True) is True


class TestDispatchCommitProjectPushCoercion:
    """Regression: ``bool("false")`` is True. The chat_commit_project
    dispatcher must coerce push the same way other boolean flags do, or a
    JSON-RPC client that stringifies booleans would silently trigger a push.
    """

    def test_string_false_does_not_push(self, tmp_path, mocker, monkeypatch):
        from src.task_queue import TaskQueue
        from src.worker_manager import WorkerManager
        from src.reset_control import TokenStore
        monkeypatch.setenv("CLAUDE_CWD", str(tmp_path))
        (tmp_path / "p").mkdir()
        db = ChatDB(str(tmp_path / "x.db"))
        queue = TaskQueue(str(tmp_path / "x.db"))
        manager = WorkerManager(
            db_path=str(tmp_path / "x.db"),
            project_root=str(tmp_path),
        )
        tokens = TokenStore()
        mocker.patch(
            "chat.project_mutations.commit_all", return_value=(True, "abc1234"),
        )
        push = mocker.patch("chat.project_mutations.push_current_branch")

        result = asyncio.run(dispatch(
            db, queue, manager, tokens,
            "chat_commit_project",
            {"project": "p", "message": "WIP", "push": "false"},
        ))
        assert result["pushed"] is False
        push.assert_not_called()

    def test_string_true_does_push(self, tmp_path, mocker, monkeypatch):
        from src.task_queue import TaskQueue
        from src.worker_manager import WorkerManager
        from src.reset_control import TokenStore
        monkeypatch.setenv("CLAUDE_CWD", str(tmp_path))
        (tmp_path / "p").mkdir()
        db = ChatDB(str(tmp_path / "x.db"))
        queue = TaskQueue(str(tmp_path / "x.db"))
        manager = WorkerManager(
            db_path=str(tmp_path / "x.db"),
            project_root=str(tmp_path),
        )
        tokens = TokenStore()
        mocker.patch(
            "chat.project_mutations.commit_all", return_value=(True, "abc1234"),
        )
        mocker.patch(
            "chat.project_mutations.push_current_branch",
            return_value=(True, "pushed"),
        )

        result = asyncio.run(dispatch(
            db, queue, manager, tokens,
            "chat_commit_project",
            {"project": "p", "message": "WIP", "push": "true"},
        ))
        assert result["pushed"] is True


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
