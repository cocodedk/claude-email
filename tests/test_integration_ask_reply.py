"""End-to-end integration tests: ask-reply flow + status meta queries.

Exercises the full flow: DB, routing, tools, and relay — without network calls.
Email sending is mocked; everything else uses real implementations.
"""
from src.chat_router import classify_email
from src.email_extract import extract_command
from chat.tools import (
    register_agent,
    list_agents,
)

from tests._integration_helpers import (
    AUTH_PREFIX,
    db,  # noqa: F401  (pytest fixture re-export)
    _make_msg,
)


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
