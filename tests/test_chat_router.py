"""Tests for email classification / routing logic (chat_router)."""
import email.message
import pytest
from src.chat_db import ChatDB
from src.chat_router import Route, classify_email, _strip_subject_prefix


AUTH_PREFIX = "AUTH:mysecret"


@pytest.fixture
def db(tmp_path):
    return ChatDB(str(tmp_path / "test.db"))


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


class TestStripSubjectPrefix:
    def test_strips_single_re_prefix(self):
        result = _strip_subject_prefix("Re: AUTH:mysecret status", AUTH_PREFIX)
        assert result == "status"

    def test_strips_multiple_re_prefixes(self):
        result = _strip_subject_prefix("Re: Re: AUTH:mysecret status", AUTH_PREFIX)
        assert result == "status"

    def test_strips_case_insensitive_re(self):
        result = _strip_subject_prefix("re: RE: AUTH:mysecret status", AUTH_PREFIX)
        assert result == "status"

    def test_strips_auth_prefix_without_re(self):
        result = _strip_subject_prefix("AUTH:mysecret do something", AUTH_PREFIX)
        assert result == "do something"

    def test_no_auth_prefix_present(self):
        result = _strip_subject_prefix("Re: status", AUTH_PREFIX)
        assert result == "status"

    def test_empty_subject(self):
        result = _strip_subject_prefix("", AUTH_PREFIX)
        assert result == ""

    def test_only_re_prefixes(self):
        result = _strip_subject_prefix("Re: Re: ", AUTH_PREFIX)
        assert result == ""

    def test_auth_prefix_with_extra_whitespace(self):
        result = _strip_subject_prefix("AUTH:mysecret   @agent-fits do it", AUTH_PREFIX)
        assert result == "@agent-fits do it"


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
        """Codex P2: subject-only @agent mails must deliver the remainder
        as body, not the whole subject (which still has the @agent prefix).
        """
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


class TestMetaCommands:
    def test_status_command(self, db):
        email_msg = _make_msg(subject=f"{AUTH_PREFIX} status", body="")
        route = classify_email(email_msg, db, AUTH_PREFIX)
        assert route.kind == "meta"
        assert route.meta_command == "status"
        assert route.meta_args == ""

    def test_spawn_without_args(self, db):
        email_msg = _make_msg(subject=f"{AUTH_PREFIX} spawn", body="")
        route = classify_email(email_msg, db, AUTH_PREFIX)
        assert route.kind == "meta"
        assert route.meta_command == "spawn"
        assert route.meta_args == ""

    def test_spawn_with_args(self, db):
        email_msg = _make_msg(
            subject=f"{AUTH_PREFIX} spawn /path/to/project build it",
            body="",
        )
        route = classify_email(email_msg, db, AUTH_PREFIX)
        assert route.kind == "meta"
        assert route.meta_command == "spawn"
        assert route.meta_args == "/path/to/project build it"

    def test_restart_command(self, db):
        email_msg = _make_msg(subject=f"{AUTH_PREFIX} restart agent-fits", body="")
        route = classify_email(email_msg, db, AUTH_PREFIX)
        assert route.kind == "meta"
        assert route.meta_command == "restart"
        assert route.meta_args == "agent-fits"

    def test_meta_with_re_prefix(self, db):
        email_msg = _make_msg(subject=f"Re: Re: {AUTH_PREFIX} status", body="")
        route = classify_email(email_msg, db, AUTH_PREFIX)
        assert route.kind == "meta"
        assert route.meta_command == "status"


class TestCliFallback:
    def test_plain_command(self, db):
        email_msg = _make_msg(
            subject=f"{AUTH_PREFIX} explain this code",
            body="def foo(): pass",
        )
        route = classify_email(email_msg, db, AUTH_PREFIX)
        assert route.kind == "cli"

    def test_unrecognized_subject(self, db):
        email_msg = _make_msg(
            subject=f"{AUTH_PREFIX} run all tests",
            body="please run tests",
        )
        route = classify_email(email_msg, db, AUTH_PREFIX)
        assert route.kind == "cli"

    def test_empty_subject_after_stripping(self, db):
        email_msg = _make_msg(
            subject=f"Re: {AUTH_PREFIX} ",
            body="something",
        )
        route = classify_email(email_msg, db, AUTH_PREFIX)
        assert route.kind == "cli"
