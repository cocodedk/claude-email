"""Tests for email classification / routing logic (chat_router)."""
import pytest
from src.chat_db import ChatDB
from src.chat_router import Route, classify_email, _strip_subject_prefix
from tests._chat_router_helpers import AUTH_PREFIX, _make_msg


@pytest.fixture
def db(tmp_path):
    return ChatDB(str(tmp_path / "test.db"))


class TestChatReply:
    def test_in_reply_to_matches_db(self, db):
        """In-Reply-To matching a known email_message_id routes as chat_reply."""
        msg_row = db.insert_message("agent-fits", "user", "hello", "reply")
        db.set_email_message_id(msg_row["id"], "<abc@example.com>")

        email_msg = _make_msg(
            subject=f"{AUTH_PREFIX} anything",
            body="follow up",
            in_reply_to="<abc@example.com>",
        )
        route = classify_email(email_msg, db, AUTH_PREFIX)
        assert route.kind == "chat_reply"
        assert route.agent_name == "agent-fits"
        assert route.original_message_id == msg_row["id"]

    def test_in_reply_to_not_in_db_falls_through(self, db):
        """In-Reply-To that doesn't match any DB row falls through to other checks."""
        email_msg = _make_msg(
            subject=f"{AUTH_PREFIX} do something",
            body="some command",
            in_reply_to="<unknown@example.com>",
        )
        route = classify_email(email_msg, db, AUTH_PREFIX)
        assert route.kind == "cli"

    def test_no_in_reply_to_header(self, db):
        """Message without In-Reply-To goes to other routing checks."""
        email_msg = _make_msg(
            subject=f"{AUTH_PREFIX} do something",
            body="run tests",
        )
        route = classify_email(email_msg, db, AUTH_PREFIX)
        assert route.kind == "cli"
