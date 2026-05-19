"""Tests for email classification / routing logic (chat_router)."""
import pytest
from src.chat_db import ChatDB
from src.chat_router import Route, classify_email, _strip_subject_prefix
from tests._chat_router_helpers import AUTH_PREFIX, _make_msg


@pytest.fixture
def db(tmp_path):
    return ChatDB(str(tmp_path / "test.db"))


class TestAgentCommand:
    def test_at_agent_name_parsed(self, db):
        """Subject with @agent-name routes as agent_command."""
        email_msg = _make_msg(
            subject=f"{AUTH_PREFIX} @agent-fits do something",
            body="the instruction body",
        )
        route = classify_email(email_msg, db, AUTH_PREFIX)
        assert route.kind == "agent_command"
        assert route.agent_name == "agent-fits"
        assert route.body == "the instruction body"

    def test_at_agent_with_re_prefix(self, db):
        """Re: prefix before @agent-name still works."""
        email_msg = _make_msg(
            subject=f"Re: {AUTH_PREFIX} @my-agent work now",
            body="do the work",
        )
        route = classify_email(email_msg, db, AUTH_PREFIX)
        assert route.kind == "agent_command"
        assert route.agent_name == "my-agent"
        assert route.body == "do the work"

    def test_at_agent_extracts_body_from_email(self, db):
        """Agent command body comes from email body, not from subject."""
        email_msg = _make_msg(
            subject=f"{AUTH_PREFIX} @agent-fits",
            body="instruction from body",
        )
        route = classify_email(email_msg, db, AUTH_PREFIX)
        assert route.kind == "agent_command"
        assert route.agent_name == "agent-fits"
        assert route.body == "instruction from body"

    def test_subject_only_agent_command_uses_subject_remainder(self, db):
        """Subject-only @agent mails deliver the remainder as body, not
        the whole subject (which still has the @agent prefix)."""
        email_msg = _make_msg(
            subject=f"{AUTH_PREFIX} @agent-fits run tests",
            body="",
        )
        route = classify_email(email_msg, db, AUTH_PREFIX)
        assert route.kind == "agent_command"
        assert route.agent_name == "agent-fits"
        assert route.body == "run tests"

    def test_subject_only_bare_agent_returns_empty_body(self, db):
        email_msg = _make_msg(
            subject=f"{AUTH_PREFIX} @agent-fits",
            body="",
        )
        route = classify_email(email_msg, db, AUTH_PREFIX)
        assert route.kind == "agent_command"
        assert route.agent_name == "agent-fits"
        assert route.body == ""
