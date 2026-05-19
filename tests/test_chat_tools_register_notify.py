"""Tests for the MCP tool handler functions (chat/tools.py).

Covers: register_agent + notify_user (including progress envelopes).
"""
from chat.tools import (
    register_agent,
    notify_user,
)
from tests._chat_tools_helpers import db, _running_json_task  # noqa: F401


# ── register_agent ────────────────────────────────────────────

class TestRegisterAgent:
    def test_returns_registered_status(self, db):
        result = register_agent(db, "agent-1", "/projects/one")
        assert result == {"status": "registered", "name": "agent-1"}

    def test_actually_creates_agent_in_db(self, db):
        register_agent(db, "agent-1", "/projects/one")
        agent = db.get_agent("agent-1")
        assert agent is not None
        assert agent["name"] == "agent-1"
        assert agent["project_path"] == "/projects/one"
        assert agent["status"] == "running"


# ── notify_user ───────────────────────────────────────────────

class TestNotifyUser:
    def test_returns_sent_status(self, db):
        db.register_agent("bot", "/p")
        result = notify_user(db, "bot", "Build done!")
        assert result == {"status": "sent"}

    def test_creates_pending_message_to_user(self, db):
        db.register_agent("bot", "/p")
        notify_user(db, "bot", "Build done!")
        msgs = db.get_pending_messages_for("user")
        assert len(msgs) == 1
        assert msgs[0]["from_name"] == "bot"
        assert msgs[0]["body"] == "Build done!"
        assert msgs[0]["type"] == "notify"

    def test_touches_agent_last_seen(self, db):
        db.register_agent("bot", "/p")
        first = db.get_agent("bot")["last_seen_at"]
        import time
        time.sleep(0.01)
        notify_user(db, "bot", "Build done!")
        second = db.get_agent("bot")["last_seen_at"]
        assert second > first

    def test_notify_with_task_id_stores_task_id(self, db):
        db.register_agent("bot", "/p")
        task_id = db._conn.execute(
            "INSERT INTO tasks (project_path, body, status, created_at) VALUES (?, ?, ?, ?)",
            ("/p", "work", "running", "2026-01-01T00:00:00"),
        ).lastrowid
        db._conn.commit()
        notify_user(db, "bot", "done", task_id=task_id)
        msgs = db.get_pending_messages_for("user")
        assert msgs[0]["task_id"] == task_id


class TestNotifyUserProgressEnvelope:
    """meta.progress on kind=progress envelopes — wire format locked
    with agent-Claude-Email-App on 2026-05-03 (B5)."""

    def test_json_task_with_progress_builds_progress_envelope(self, db):
        import json
        db.register_agent("bot", "/p")
        task_id = _running_json_task(db)
        notify_user(
            db, "bot", "Running tests", task_id=task_id,
            progress={"current": 3, "total": 7, "label": "tests passed"},
        )
        msg = db.get_pending_messages_for("user")[0]
        assert msg["content_type"] == "application/json"
        env = json.loads(msg["body"])
        assert env["kind"] == "progress"
        assert env["body"] == "Running tests"
        assert env["meta"]["progress"] == {
            "current": 3, "total": 7, "label": "tests passed",
        }

    def test_plain_origin_task_drops_progress_keeps_plain_text(self, db):
        """Plain-text origin (no app) doesn't get JSON envelopes — same
        branch-on-origin pattern as task_notifier. Progress is dropped."""
        db.register_agent("bot", "/p")
        # Plain-origin task — no origin_content_type set.
        task_id = db._conn.execute(
            "INSERT INTO tasks (project_path, body, status, created_at) VALUES (?, ?, ?, ?)",
            ("/p", "work", "running", "2026-01-01T00:00:00"),
        ).lastrowid
        db._conn.commit()
        notify_user(
            db, "bot", "Running tests", task_id=task_id,
            progress={"current": 3, "total": 7},
        )
        msg = db.get_pending_messages_for("user")[0]
        assert (msg["content_type"] or "") == ""
        assert msg["body"] == "Running tests"

    def test_no_task_id_keeps_plain_text(self, db):
        """Without task_id we can't determine origin — stay plain text."""
        db.register_agent("bot", "/p")
        notify_user(
            db, "bot", "tick", progress={"current": 1, "total": 2},
        )
        msg = db.get_pending_messages_for("user")[0]
        assert (msg["content_type"] or "") == ""
        assert msg["body"] == "tick"

    def test_progress_none_keeps_plain_text_on_json_task(self, db):
        """Backward compat: progress not provided → existing behavior."""
        db.register_agent("bot", "/p")
        task_id = _running_json_task(db)
        notify_user(db, "bot", "Build done!", task_id=task_id)
        msg = db.get_pending_messages_for("user")[0]
        assert (msg["content_type"] or "") == ""
        assert msg["body"] == "Build done!"

    def test_invalid_fields_silently_dropped(self, db):
        import json
        db.register_agent("bot", "/p")
        task_id = _running_json_task(db)
        notify_user(
            db, "bot", "tick", task_id=task_id,
            progress={
                "current": -1,        # drop: negative
                "total": "seven",     # drop: non-int
                "percent": 42.5,      # keep
                "label": "ok",        # keep
                "extra": "junk",      # drop: unknown key
            },
        )
        msg = db.get_pending_messages_for("user")[0]
        env = json.loads(msg["body"])
        assert env["meta"]["progress"] == {"percent": 42.5, "label": "ok"}

    def test_percent_out_of_range_dropped(self, db):
        import json
        db.register_agent("bot", "/p")
        task_id = _running_json_task(db)
        notify_user(
            db, "bot", "tick", task_id=task_id,
            progress={"percent": 150, "label": "ok"},
        )
        env = json.loads(db.get_pending_messages_for("user")[0]["body"])
        assert env["meta"]["progress"] == {"label": "ok"}

    def test_label_too_long_dropped(self, db):
        import json
        db.register_agent("bot", "/p")
        task_id = _running_json_task(db)
        notify_user(
            db, "bot", "tick", task_id=task_id,
            progress={"current": 1, "label": "x" * 201},
        )
        env = json.loads(db.get_pending_messages_for("user")[0]["body"])
        assert env["meta"]["progress"] == {"current": 1}

    def test_all_fields_invalid_falls_back_to_plain_text(self, db):
        """If filtering empties the dict, treat as no progress at all."""
        db.register_agent("bot", "/p")
        task_id = _running_json_task(db)
        notify_user(
            db, "bot", "tick", task_id=task_id,
            progress={"current": -1, "percent": 999},
        )
        msg = db.get_pending_messages_for("user")[0]
        assert (msg["content_type"] or "") == ""
        assert msg["body"] == "tick"
