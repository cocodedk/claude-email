"""Tests for email classification / routing logic (chat_router)."""
import pytest
from src.chat_db import ChatDB
from src.chat_router import Route, classify_email, _strip_subject_prefix
from tests._chat_router_helpers import AUTH_PREFIX, _make_msg


@pytest.fixture
def db(tmp_path):
    return ChatDB(str(tmp_path / "test.db"))


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
