"""Tests for ChatDB read-only dashboard queries (DashboardQueriesMixin)."""
import pytest

from src.chat_db import ChatDB


@pytest.fixture
def db(tmp_path):
    return ChatDB(str(tmp_path / "test.db"))


class TestAgentsSummary:
    def test_empty(self, db):
        assert db.get_agents_summary() == []

    def test_lists_registered_agents(self, db):
        db.register_agent("a1", "/p1")
        db.register_agent("a2", "/p2")
        result = db.get_agents_summary()
        names = {r["name"] for r in result}
        assert names == {"a1", "a2"}

    def test_includes_status_pid_and_last_seen(self, db):
        db.register_agent("bot", "/p", pid=1234)
        [row] = db.get_agents_summary()
        assert row["status"] == "running"
        assert row["pid"] == 1234
        assert row["project_path"] == "/p"
        assert "last_seen_at" in row

    def test_orders_newest_first(self, db):
        db.register_agent("first", "/p1")
        db.register_agent("second", "/p2")
        names = [r["name"] for r in db.get_agents_summary()]
        # last_seen_at DESC — most recent registration first
        assert names[0] == "second"

    def test_hides_stale_agents(self, db):
        """Agents whose last_seen_at is older than stale_secs are ghosts —
        an MCP-registered agent with pid=NULL that crashed will never be
        picked up by pid-based reaping, so the dashboard filters them by
        heartbeat instead. The DB row stays (ownership claims still apply);
        only the visible projection drops them."""
        db.register_agent("ghost", "/p1")
        # Backdate ghost's last_seen_at beyond the cutoff.
        db._conn.execute(
            "UPDATE agents SET last_seen_at=? WHERE name=?",
            ("1970-01-01T00:00:00+00:00", "ghost"),
        )
        db._conn.commit()
        db.register_agent("fresh", "/p2")
        names = [r["name"] for r in db.get_agents_summary()]
        assert names == ["fresh"]
        # But the stored row is still there — ownership logic isn't affected.
        assert db.get_agent("ghost") is not None

    def test_stale_threshold_is_configurable(self, db):
        """A caller that wants the full picture (e.g. chat_list_agents)
        can pass a huge threshold to disable filtering."""
        db.register_agent("anyone", "/p1")
        db._conn.execute(
            "UPDATE agents SET last_seen_at=? WHERE name=?",
            ("1970-01-01T00:00:00+00:00", "anyone"),
        )
        db._conn.commit()
        assert db.get_agents_summary() == []
        # 100 years of slack — agent from 1970 is "fresh enough"
        assert db.get_agents_summary(stale_secs=3600 * 24 * 365 * 100) != []


class TestMessagesSummary:
    def test_empty(self, db):
        assert db.get_messages_summary() == []

    def test_returns_all_fields(self, db):
        db.insert_message("alice", "bob", "hi", "notify")
        [row] = db.get_messages_summary()
        assert row["from_name"] == "alice"
        assert row["to_name"] == "bob"
        assert row["body"] == "hi"
        assert row["type"] == "notify"
        assert row["status"] == "pending"
        assert "created_at" in row
        assert "id" in row

    def test_orders_newest_first(self, db):
        db.insert_message("a", "b", "one", "notify")
        db.insert_message("a", "b", "two", "notify")
        rows = db.get_messages_summary()
        assert rows[0]["body"] == "two"
        assert rows[1]["body"] == "one"

    def test_respects_limit(self, db):
        for i in range(5):
            db.insert_message("a", "b", f"msg-{i}", "notify")
        assert len(db.get_messages_summary(limit=2)) == 2
        assert len(db.get_messages_summary(limit=100)) == 5


class TestMessagesSince:
    def test_empty(self, db):
        assert db.get_messages_since(0) == []

    def test_returns_rows_after_watermark(self, db):
        m1 = db.insert_message("a", "b", "one", "notify")
        m2 = db.insert_message("a", "b", "two", "notify")
        m3 = db.insert_message("a", "b", "three", "notify")
        rows = db.get_messages_since(m1["id"])
        assert [r["id"] for r in rows] == [m2["id"], m3["id"]]

    def test_orders_ascending(self, db):
        for i in range(3):
            db.insert_message("a", "b", f"m{i}", "notify")
        rows = db.get_messages_since(0)
        ids = [r["id"] for r in rows]
        assert ids == sorted(ids)

    def test_respects_limit(self, db):
        for i in range(5):
            db.insert_message("a", "b", f"m{i}", "notify")
        rows = db.get_messages_since(0, limit=2)
        assert len(rows) == 2


class TestLatestMessageId:
    def test_empty_returns_zero(self, db):
        assert db.latest_message_id() == 0

    def test_returns_max_id(self, db):
        db.insert_message("a", "b", "first", "notify")
        latest = db.insert_message("a", "b", "last", "notify")
        assert db.latest_message_id() == latest["id"]


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
