"""End-to-end integration tests: notify + command-dispatch flows.

Exercises the full flow: DB, routing, tools, and relay — without network calls.
Email sending is mocked; everything else uses real implementations.
"""
from src.chat_router import classify_email
from chat.tools import (
    register_agent,
    notify_user,
    check_messages,
)

from tests._integration_helpers import (
    AUTH_PREFIX,
    db,  # noqa: F401  (pytest fixture re-export)
    _make_msg,
)


# ── Test 1: Full Notify Flow ────────────────────────────────

class TestFullNotifyFlow:
    def test_agent_notifies_user_message_appears_pending(self, db):
        """Agent notifies user -> message appears pending for user."""
        register_agent(db, "agent-notify", "/projects/notify")
        notify_user(db, "agent-notify", "Build completed successfully")

        pending = db.get_pending_messages_for("user")
        assert len(pending) == 1
        msg = pending[0]
        assert msg["from_name"] == "agent-notify"
        assert msg["to_name"] == "user"
        assert msg["type"] == "notify"
        assert msg["body"] == "Build completed successfully"
        assert msg["status"] == "pending"


# ── Test 2: Full Command Dispatch Flow ──────────────────────

class TestFullCommandDispatchFlow:
    def test_user_sends_agent_command_agent_picks_it_up(self, db):
        """User sends @agent-name command -> agent picks it up."""
        register_agent(db, "agent-fits", "/projects/fits")

        email_msg = _make_msg(
            subject=f"{AUTH_PREFIX} @agent-fits refactor the auth module",
            body="refactor the auth module",
        )

        route = classify_email(email_msg, db, AUTH_PREFIX)
        assert route.kind == "agent_command"
        assert route.agent_name == "agent-fits"

        # Insert command message in DB (as handle_chat_email would)
        db.insert_message("user", route.agent_name, route.body, "command")

        # Agent picks up the command
        result = check_messages(db, "agent-fits")
        assert len(result["messages"]) == 1
        cmd_msg = result["messages"][0]
        assert cmd_msg["from"] == "user"
        assert cmd_msg["type"] == "command"
        assert cmd_msg["body"] == "refactor the auth module"
