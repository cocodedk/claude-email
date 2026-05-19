"""Tests for the branch-reuse + guards path in src/reply_router.apply_reply.

Lookup chain: In-Reply-To → outbound_emails.task_id → tasks row →
branch_name + mutates_repo. Guards: project_path must match, and
outbound.sender_agent must match agent_name."""
import pytest

from src.chat_db import ChatDB
from src.reply_router import apply_reply
from src.task_queue import TaskQueue

from tests._apply_reply_branch_reuse_helpers import (
    _StubWM,
    _latest,
    _project_dir,
)


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "db")
    ChatDB(path)
    return path


@pytest.fixture
def db(db_path):
    return ChatDB(db_path)


@pytest.fixture
def tq(db_path):
    return TaskQueue(db_path)


class TestClassifierIntegration:
    def test_mutating_body_stamps_true(self, db, tq, tmp_path):
        proj = _project_dir(tmp_path)
        db.register_agent("agent-p", proj)
        original = db.insert_message("agent-p", "user", "x", "notify")
        apply_reply(
            db, tq, _StubWM(),
            agent_name="agent-p", original_message_id=original["id"],
            body="fix the bus",
            allowed_base=str(tmp_path),
            original_email_message_id="",
        )
        assert _latest(tq)["mutates_repo"] == 1

    def test_read_only_body_stamps_false(self, db, tq, tmp_path):
        proj = _project_dir(tmp_path)
        db.register_agent("agent-p", proj)
        original = db.insert_message("agent-p", "user", "x", "notify")
        apply_reply(
            db, tq, _StubWM(),
            agent_name="agent-p", original_message_id=original["id"],
            body="explain the relay",
            allowed_base=str(tmp_path),
            original_email_message_id="",
        )
        new = _latest(tq)
        assert new["mutates_repo"] == 0

    def test_empty_body_leaves_mutates_null(self, db, tq, tmp_path):
        proj = _project_dir(tmp_path)
        db.register_agent("agent-p", proj)
        original = db.insert_message("agent-p", "user", "x", "notify")
        apply_reply(
            db, tq, _StubWM(),
            agent_name="agent-p", original_message_id=original["id"],
            body="",
            allowed_base=str(tmp_path),
            original_email_message_id="",
        )
        assert _latest(tq)["mutates_repo"] is None


class TestAckText:
    def test_read_only_ack_says_no_branch(self, db, tq, tmp_path):
        proj = _project_dir(tmp_path)
        db.register_agent("agent-p", proj)
        original = db.insert_message("agent-p", "user", "x", "notify")
        ack, _tag = apply_reply(
            db, tq, _StubWM(),
            agent_name="agent-p", original_message_id=original["id"],
            body="show me the schema",
            allowed_base=str(tmp_path),
            original_email_message_id="",
        )
        assert "read-only" in ack.lower()
        assert "no branch" in ack.lower()

    def test_planned_branch_ack_for_new_mutating_task(
        self, db, tq, tmp_path,
    ):
        proj = _project_dir(tmp_path)
        db.register_agent("agent-p", proj)
        original = db.insert_message("agent-p", "user", "x", "notify")
        ack, _tag = apply_reply(
            db, tq, _StubWM(),
            agent_name="agent-p", original_message_id=original["id"],
            body="implement the new endpoint",
            allowed_base=str(tmp_path),
            original_email_message_id="",
        )
        assert "planned branch" in ack
        assert "claude/task-" in ack
