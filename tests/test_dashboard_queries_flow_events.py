"""Tests for ChatDB read-only dashboard queries (DashboardQueriesMixin)."""
import pytest

from src.chat_db import ChatDB


@pytest.fixture
def db(tmp_path):
    return ChatDB(str(tmp_path / "test.db"))


class TestFlowEventQueries:
    """The dashboard's technical-flow panel polls the events table for
    the specific event_types that drive its animations. These queries
    must skip any unrelated events already written by the messaging and
    register paths."""

    def test_events_since_filters_to_flow_types(self, db):
        # Normal traffic logs 'message' and 'register' events which must
        # NOT leak into the flow-panel stream.
        db.register_agent("bot", "/p")
        db.insert_message("bot", "peer", "hi", "notify")
        # And the real flow events we care about:
        db._log_event("bot", "wake_spawn_start", "resume=False")
        db._log_event("bot", "hook_drain_stop", "drained=1")
        rows = db.get_flow_events_since(0)
        types = [r["event_type"] for r in rows]
        assert types == ["wake_spawn_start", "hook_drain_stop"]

    def test_events_since_watermark(self, db):
        a = db._log_event("bot", "wake_spawn_start", "1")
        db._log_event("bot", "wake_spawn_end", "2")
        # latest id just after the first wake_spawn_start event
        latest = db.latest_flow_event_id()
        assert latest > 0
        # Watermark at latest_flow_event_id → no rows yet
        assert db.get_flow_events_since(latest) == []
        db._log_event("bot", "hook_drain_stop", "3")
        rows = db.get_flow_events_since(latest)
        assert [r["event_type"] for r in rows] == ["hook_drain_stop"]

    def test_latest_flow_event_id_empty(self, db):
        db.insert_message("a", "b", "hi", "notify")  # noise only
        assert db.latest_flow_event_id() == 0

    def test_latest_flow_event_id_tracks_max(self, db):
        db._log_event("bot", "wake_spawn_start", "x")
        db._log_event("bot", "hook_drain_stop", "y")
        last = db.latest_flow_event_id()
        assert last > 0
        # Adding a non-flow event (e.g. a registration) must not move the max
        db.register_agent("other", "/p")
        assert db.latest_flow_event_id() == last

    def test_events_since_respects_limit(self, db):
        for i in range(5):
            db._log_event("bot", "wake_spawn_start", f"i={i}")
        rows = db.get_flow_events_since(0, limit=2)
        assert len(rows) == 2
