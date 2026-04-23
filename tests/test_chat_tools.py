"""Tests for the MCP tool handler functions (chat/tools.py)."""
import asyncio
import pytest
from src.chat_db import ChatDB
from chat.tools import (
    register_agent,
    notify_user,
    ask_user,
    check_messages,
    list_agents,
    deregister_agent,
    message_agent,
)


@pytest.fixture
def db(tmp_path):
    return ChatDB(str(tmp_path / "test.db"))


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
