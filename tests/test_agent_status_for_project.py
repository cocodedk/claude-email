"""``ChatDB.agent_status_for_project``: 3-state liveness for the
``list_projects`` envelope's ``agent_status`` field."""
from datetime import datetime, timedelta, timezone

import pytest

from src.chat_db import ChatDB


@pytest.fixture
def db(tmp_path):
    return ChatDB(str(tmp_path / "test.db"))


def _force_last_seen(db: ChatDB, name: str, when_iso: str) -> None:
    """Mutate ``last_seen_at`` directly so we can simulate a stale agent
    without sleeping. ``register_agent`` always writes ``now()``."""
    db._conn.execute(
        "UPDATE agents SET last_seen_at=? WHERE name=?", (when_iso, name),
    )
    db._conn.commit()


class TestAgentStatusForProject:
    def test_absent_when_no_row(self, db):
        assert db.agent_status_for_project("/does/not/exist") == "absent"

    def test_connected_when_running_and_fresh(self, db):
        db.register_agent("alpha", "/p")
        assert db.agent_status_for_project("/p") == "connected"

    def test_disconnected_when_status_disconnected(self, db):
        db.register_agent("alpha", "/p")
        db.update_agent_status("alpha", "disconnected")
        assert db.agent_status_for_project("/p") == "disconnected"

    def test_disconnected_when_running_but_stale(self, db):
        db.register_agent("alpha", "/p")
        stale = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
        _force_last_seen(db, "alpha", stale)
        assert db.agent_status_for_project("/p") == "disconnected"

    def test_connected_with_multiple_agents_one_live(self, db):
        """Multi-agent same project — connected if ANY is live + fresh."""
        db.register_agent("laptop", "/p")
        db.register_agent("desktop", "/p")
        # Make laptop stale; desktop still fresh.
        stale = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
        _force_last_seen(db, "laptop", stale)
        assert db.agent_status_for_project("/p") == "connected"

    def test_disconnected_with_multiple_agents_all_stale(self, db):
        db.register_agent("laptop", "/p")
        db.register_agent("desktop", "/p")
        stale = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
        _force_last_seen(db, "laptop", stale)
        _force_last_seen(db, "desktop", stale)
        assert db.agent_status_for_project("/p") == "disconnected"

    def test_freshness_window_param_overrides_default(self, db):
        """A 5-second window treats a 30-second-old heartbeat as stale."""
        db.register_agent("alpha", "/p")
        thirty_s = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
        _force_last_seen(db, "alpha", thirty_s)
        assert db.agent_status_for_project("/p", freshness_sec=5) == "disconnected"
        # Default 60s window still treats it as connected.
        assert db.agent_status_for_project("/p") == "connected"

    def test_does_not_match_substring_paths(self, db):
        """``/p/sub`` registered should NOT count as live for ``/p``."""
        db.register_agent("alpha", "/p/sub")
        assert db.agent_status_for_project("/p") == "absent"
        assert db.agent_status_for_project("/p/sub") == "connected"
