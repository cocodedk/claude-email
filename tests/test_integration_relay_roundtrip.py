"""End-to-end integration tests: relay Message-ID storage + full round trip.

Exercises the full flow: DB, routing, tools, and relay — without network calls.
Email sending is mocked; everything else uses real implementations.
"""
from unittest.mock import patch

from src.chat_router import classify_email
from src.chat_handlers import relay_outbound_messages
from src.email_extract import extract_command
from chat.tools import register_agent

from tests._integration_helpers import (
    AUTH_PREFIX,
    DUMMY_CONFIG,
    db,  # noqa: F401  (pytest fixture re-export)
    _make_msg,
)


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
