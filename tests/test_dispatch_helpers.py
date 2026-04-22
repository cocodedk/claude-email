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
