"""Tests for email classification / routing logic (chat_router)."""
import pytest
from src.chat_db import ChatDB
from src.chat_router import Route, classify_email, _strip_subject_prefix
from tests._chat_router_helpers import AUTH_PREFIX, _make_msg


@pytest.fixture
def db(tmp_path):
    return ChatDB(str(tmp_path / "test.db"))


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
