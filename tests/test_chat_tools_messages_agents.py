"""Tests for the MCP tool handler functions (chat/tools.py).

Covers: check_messages, list_agents, message_agent, deregister_agent.
"""
from chat.tools import (
    check_messages,
    list_agents,
    deregister_agent,
    message_agent,
)
from tests._chat_tools_helpers import db  # noqa: F401


# ── check_messages ────────────────────────────────────────────

class TestCheckMessages:
    def test_returns_pending_messages(self, db):
        db.register_agent("bot", "/p")
        db.insert_message("user", "bot", "hello", "notify")
        db.insert_message("user", "bot", "world", "notify")
        result = check_messages(db, "bot")
        assert len(result["messages"]) == 2
        assert result["messages"][0]["body"] == "hello"
        assert result["messages"][1]["body"] == "world"

    def test_message_shape(self, db):
        db.register_agent("bot", "/p")
        db.insert_message("user", "bot", "hi", "notify")
        result = check_messages(db, "bot")
        msg = result["messages"][0]
        assert set(msg.keys()) == {"id", "from", "body", "type", "created_at"}
        assert msg["from"] == "user"
        assert msg["body"] == "hi"
        assert msg["type"] == "notify"

    def test_marks_messages_as_delivered(self, db):
        db.register_agent("bot", "/p")
        db.insert_message("user", "bot", "hi", "notify")
        check_messages(db, "bot")
        # Second call should return empty — messages already delivered
        result = check_messages(db, "bot")
        assert result["messages"] == []

    def test_touches_agent_last_seen(self, db):
        db.register_agent("bot", "/p")
        first = db.get_agent("bot")["last_seen_at"]
        import time
        time.sleep(0.01)
        check_messages(db, "bot")
        second = db.get_agent("bot")["last_seen_at"]
        assert second >= first

    def test_empty_when_no_messages(self, db):
        db.register_agent("bot", "/p")
        result = check_messages(db, "bot")
        assert result == {"messages": []}


# ── list_agents ───────────────────────────────────────────────

class TestListAgents:
    def test_returns_empty_list(self, db):
        result = list_agents(db)
        assert result == {"agents": []}

    def test_returns_agent_details(self, db):
        db.register_agent("a1", "/p1")
        db.register_agent("a2", "/p2")
        result = list_agents(db)
        assert len(result["agents"]) == 2
        names = {a["name"] for a in result["agents"]}
        assert names == {"a1", "a2"}

    def test_agent_shape(self, db):
        db.register_agent("a1", "/p1")
        result = list_agents(db)
        agent = result["agents"][0]
        assert set(agent.keys()) == {"name", "status", "project_path", "last_seen_at"}


# ── message_agent ─────────────────────────────────────────────

class TestMessageAgent:
    def test_delivers_to_registered_peer(self, db):
        db.register_agent("a-sender", "/p/s")
        db.register_agent("a-recipient", "/p/r")
        result = message_agent(db, "a-sender", "a-recipient", "ping")
        assert result == {"status": "sent", "to": "a-recipient"}
        pending = db.get_pending_messages_for("a-recipient")
        assert len(pending) == 1
        assert pending[0]["from_name"] == "a-sender"
        assert pending[0]["body"] == "ping"
        assert pending[0]["type"] == "notify"

    def test_rejects_user_recipient(self, db):
        """'user' goes via chat_notify — two paths confuse the model."""
        db.register_agent("a-sender", "/p")
        result = message_agent(db, "a-sender", "user", "hi")
        assert "error" in result
        assert "chat_notify" in result["error"]
        # No message inserted
        assert db.get_pending_messages_for("user") == []

    def test_rejects_unknown_recipient(self, db):
        """Typos shouldn't silently queue ghost messages."""
        db.register_agent("a-sender", "/p")
        result = message_agent(db, "a-sender", "agent-typo", "hi")
        assert "error" in result
        assert "agent-typo" in result["error"]
        # No message inserted for the ghost
        assert db.get_pending_messages_for("agent-typo") == []

    def test_rejects_empty_recipient(self, db):
        db.register_agent("a-sender", "/p")
        result = message_agent(db, "a-sender", "", "hi")
        assert "error" in result

    def test_touches_agent_last_seen(self, db):
        db.register_agent("a-sender", "/p/s")
        db.register_agent("a-recipient", "/p/r")
        first = db.get_agent("a-sender")["last_seen_at"]
        import time
        time.sleep(0.01)
        message_agent(db, "a-sender", "a-recipient", "ping")
        second = db.get_agent("a-sender")["last_seen_at"]
        assert second > first

    def test_touches_agent_even_on_rejected_recipient(self, db):
        """Caller is alive regardless of typos — heartbeat still refreshes."""
        db.register_agent("a-sender", "/p/s")
        first = db.get_agent("a-sender")["last_seen_at"]
        import time
        time.sleep(0.01)
        message_agent(db, "a-sender", "agent-typo", "hi")
        second = db.get_agent("a-sender")["last_seen_at"]
        assert second > first

    def test_message_with_task_id_stores_task_id(self, db):
        """Threads peer-to-peer messages back to the originating task,
        matching notify_user / ask_user behaviour."""
        db.register_agent("a-sender", "/p/s")
        db.register_agent("a-recipient", "/p/r")
        task_id = db._conn.execute(
            "INSERT INTO tasks (project_path, body, status, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("/p/s", "work", "running", "2026-01-01T00:00:00"),
        ).lastrowid
        db._conn.commit()
        message_agent(db, "a-sender", "a-recipient", "ping", task_id=task_id)
        pending = db.get_pending_messages_for("a-recipient")
        assert pending[0]["task_id"] == task_id


# ── deregister_agent ──────────────────────────────────────────

class TestDeregisterAgent:
    def test_returns_deregistered_status(self, db):
        db.register_agent("bot", "/p")
        result = deregister_agent(db, "bot")
        assert result == {"status": "deregistered"}

    def test_actually_updates_db(self, db):
        db.register_agent("bot", "/p")
        deregister_agent(db, "bot")
        agent = db.get_agent("bot")
        assert agent["status"] == "deregistered"
