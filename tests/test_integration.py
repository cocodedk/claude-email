"""End-to-end integration tests for the chat system.

Exercises the full flow: DB, routing, tools, and relay — without network calls.
Email sending is mocked; everything else uses real implementations.
"""
import email.message
from unittest.mock import patch

import pytest

from src.chat_db import ChatDB
from src.chat_router import Route, classify_email
from src.chat_handlers import relay_outbound_messages
from src.email_extract import extract_command
from chat.tools import (
    register_agent,
    notify_user,
    check_messages,
    list_agents,
)

AUTH_PREFIX = "AUTH:testsecret"

DUMMY_CONFIG = {
    "smtp_host": "smtp.example.com",
    "smtp_port": 465,
    "username": "bot@example.com",
    "password": "fake-password",
    "authorized_sender": "user@example.com",
    "auth_prefix": AUTH_PREFIX,
}


@pytest.fixture
def db(tmp_path):
    return ChatDB(str(tmp_path / "integration.db"))


def _make_msg(
    subject: str = "",
    body: str = "",
    in_reply_to: str = "",
) -> email.message.EmailMessage:
    msg = email.message.EmailMessage()
    if subject:
        msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if body:
        msg.set_content(body)
    return msg


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


# ── Test 3: Full Ask-Reply Flow ─────────────────────────────

class TestFullAskReplyFlow:
    def test_agent_asks_user_replies_agent_gets_reply(self, db):
        """Agent asks -> user replies -> agent gets reply."""
        register_agent(db, "agent-ask", "/projects/ask")

        # Agent creates an ask message
        ask_msg = db.insert_message("agent-ask", "user", "May I proceed?", "ask")
        ask_id = ask_msg["id"]

        # Simulate relay setting the email_message_id
        fake_email_id = "<ask-123@example.com>"
        db.set_email_message_id(ask_id, fake_email_id)

        # User replies via email with matching In-Reply-To
        reply_email = _make_msg(
            subject=f"Re: {AUTH_PREFIX} [agent-ask] May I proceed?",
            body="yes, go ahead",
            in_reply_to=fake_email_id,
        )

        # Route the reply
        route = classify_email(reply_email, db, AUTH_PREFIX)
        assert route.kind == "chat_reply"
        assert route.agent_name == "agent-ask"
        assert route.original_message_id == ask_id

        # Extract body and insert reply (as handle_chat_email would)
        reply_body = extract_command(reply_email)
        db.insert_message(
            "user", route.agent_name, reply_body, "reply",
            in_reply_to=route.original_message_id,
        )

        # Agent retrieves the reply
        reply = db.get_reply_to_message(ask_id)
        assert reply is not None
        assert reply["body"] == "yes, go ahead"
        assert reply["type"] == "reply"
        assert reply["from_name"] == "user"
        assert reply["in_reply_to"] == ask_id


# ── Test 4: Status Meta Query ───────────────────────────────

class TestStatusMetaQuery:
    def test_list_agents_returns_all_registered(self, db):
        """Register two agents -> list_agents returns both."""
        register_agent(db, "agent-alpha", "/projects/alpha")
        register_agent(db, "agent-beta", "/projects/beta")

        agents = db.list_agents()
        assert len(agents) == 2
        names = {a["name"] for a in agents}
        assert names == {"agent-alpha", "agent-beta"}

    def test_list_agents_via_tool(self, db):
        """list_agents tool function also returns both agents."""
        register_agent(db, "agent-alpha", "/projects/alpha")
        register_agent(db, "agent-beta", "/projects/beta")

        result = list_agents(db)
        assert len(result["agents"]) == 2
        names = {a["name"] for a in result["agents"]}
        assert names == {"agent-alpha", "agent-beta"}


# ── Test 5: Relay Stores Email Message-ID ────────────────────

class TestRelayStoresEmailMessageId:
    @patch("src.chat_relay.send_reply")
    def test_relay_stores_message_id_in_db(self, mock_send, db):
        """Relay sends email and stores returned Message-ID in DB."""
        fake_msg_id = "<relay-456@example.com>"
        mock_send.return_value = fake_msg_id

        # Establish email-origin context (user previously emailed @agent-relay)
        # so the relay gate accepts this notify. CLI-only chat_notify is
        # intentionally dropped to prevent unsolicited mail.
        db.insert_message("user", "agent-relay", "kick off", "command")
        msg = db.insert_message("agent-relay", "user", "Task finished", "notify")

        relay_outbound_messages(DUMMY_CONFIG, db)

        # Verify send_reply was called
        mock_send.assert_called_once()

        # Verify email_message_id is stored
        updated = db.find_message_by_email_id(fake_msg_id)
        assert updated is not None
        assert updated["id"] == msg["id"]

        # Verify message is marked as delivered
        pending = db.get_pending_messages_for("user")
        assert len(pending) == 0


# ── Test 6: Full Round Trip ─────────────────────────────────

class TestFullRoundTrip:
    @patch("src.chat_relay.send_reply")
    def test_agent_asks_email_sent_user_replies_agent_gets_answer(
        self, mock_send, db,
    ):
        """Full round trip: agent asks -> email sent -> user replies -> agent gets answer."""
        fake_email_id = "<roundtrip-789@example.com>"
        mock_send.return_value = fake_email_id

        # Step 1: Register agent
        register_agent(db, "agent-rt", "/projects/roundtrip")

        # Step 2: Agent inserts ask message (pending to user)
        ask_msg = db.insert_message("agent-rt", "user", "Should I deploy?", "ask")
        ask_id = ask_msg["id"]

        # Step 3: Relay sends the email (mocked)
        relay_outbound_messages(DUMMY_CONFIG, db)
        mock_send.assert_called_once()

        # Step 4: Verify email_message_id is stored on the ask
        stored = db.find_message_by_email_id(fake_email_id)
        assert stored is not None
        assert stored["id"] == ask_id

        # Step 5: User replies via email with In-Reply-To matching
        reply_email = _make_msg(
            subject="Re: [agent-rt] Should I deploy?",
            body="yes, deploy to production",
            in_reply_to=fake_email_id,
        )

        # Step 6: Route the reply through classify_email
        route = classify_email(reply_email, db, AUTH_PREFIX)
        assert route.kind == "chat_reply"
        assert route.agent_name == "agent-rt"
        assert route.original_message_id == ask_id

        # Step 7: Insert reply message (as handle_chat_email would)
        reply_body = extract_command(reply_email)
        db.insert_message(
            "user", route.agent_name, reply_body, "reply",
            in_reply_to=route.original_message_id,
        )

        # Step 8: Agent retrieves the reply
        reply = db.get_reply_to_message(ask_id)
        assert reply is not None
        assert reply["body"] == "yes, deploy to production"
        assert reply["type"] == "reply"
        assert reply["from_name"] == "user"
