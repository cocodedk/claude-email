"""Tests for the branch-reuse + guards path in src/reply_router.apply_reply.

Lookup chain: In-Reply-To → outbound_emails.task_id → tasks row →
branch_name + mutates_repo. Guards: project_path must match, and
outbound.sender_agent must match agent_name."""
import pytest

from src.chat_db import ChatDB
from src.git_ops import current_branch
from src.reply_router import apply_reply
from src.task_queue import TaskQueue

from tests._apply_reply_branch_reuse_helpers import (
    _StubWM,
    _git,
    _latest,
    _project_dir,
    _seed_prior_task,
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


class TestBranchReuseFromOutbound:
    def test_reuses_prior_branch_for_mutating_followup(
        self, db, tq, tmp_path,
    ):
        proj = _project_dir(tmp_path)
        db.register_agent("agent-p", proj)
        prior_id, out_id = _seed_prior_task(
            db, tq, proj, "claude/task-17-fix-bus", mutating=True,
        )
        original = db.insert_message(
            "agent-p", "user", "done", "notify", task_id=prior_id,
        )
        ack, _tag = apply_reply(
            db, tq, _StubWM(pid=222),
            agent_name="agent-p", original_message_id=original["id"],
            body="also add docs",
            allowed_base=str(tmp_path),
            original_email_message_id=out_id,
        )
        new = _latest(tq)
        assert new["branch_name"] == "claude/task-17-fix-bus"
        assert new["mutates_repo"] == 1
        assert "continue prior branch" in ack
        assert "claude/task-17-fix-bus" in ack

    def test_read_only_followup_after_mutating_task_reuses_branch(
        self, db, tq, tmp_path,
    ):
        proj = _project_dir(tmp_path)
        db.register_agent("agent-p", proj)
        prior_id, out_id = _seed_prior_task(
            db, tq, proj, "claude/task-17-fix-bus", mutating=True,
        )
        original = db.insert_message(
            "agent-p", "user", "done", "notify", task_id=prior_id,
        )
        apply_reply(
            db, tq, _StubWM(),
            agent_name="agent-p", original_message_id=original["id"],
            body="explain what you changed",
            allowed_base=str(tmp_path),
            original_email_message_id=out_id,
        )
        new = _latest(tq)
        assert new["branch_name"] == "claude/task-17-fix-bus"
        assert new["mutates_repo"] == 0

    def test_no_outbound_match_falls_through(self, db, tq, tmp_path):
        proj = _project_dir(tmp_path)
        db.register_agent("agent-p", proj)
        original = db.insert_message("agent-p", "user", "done", "notify")
        ack, _tag = apply_reply(
            db, tq, _StubWM(),
            agent_name="agent-p", original_message_id=original["id"],
            body="add docs",
            allowed_base=str(tmp_path),
            original_email_message_id="<never-sent@x>",
        )
        new = _latest(tq)
        assert new["branch_name"] is None
        assert "planned branch" in ack

    def test_taskless_peer_thread_reuses_current_task_branch(
        self, db, tq, tmp_path,
    ):
        proj = _project_dir(tmp_path)
        _git(proj, "init")
        _git(proj, "config", "user.email", "test@example.com")
        _git(proj, "config", "user.name", "Test User")
        (tmp_path / "p" / "README.md").write_text("x\n")
        _git(proj, "add", "README.md")
        _git(proj, "commit", "-m", "init", "--no-gpg-sign")
        _git(proj, "checkout", "-b", "claude/task-42-peer-work")
        assert current_branch(proj) == "claude/task-42-peer-work"
        db.register_agent("agent-p", proj)
        original = db.insert_message("agent-p", "user", "done", "notify")
        db.record_outbound_email(
            "<peer@x>", kind="notify", sender_agent="agent-p",
        )
        ack, _tag = apply_reply(
            db, tq, _StubWM(),
            agent_name="agent-p", original_message_id=original["id"],
            body="also add docs",
            allowed_base=str(tmp_path),
            original_email_message_id="<peer@x>",
        )
        new = _latest(tq)
        assert new["branch_name"] == "claude/task-42-peer-work"
        assert "continue prior branch" in ack

    def test_taskless_peer_thread_rejects_non_task_current_branch(
        self, db, tq, tmp_path,
    ):
        proj = _project_dir(tmp_path)
        _git(proj, "init")
        # Empty-repo `checkout -b` is unborn-HEAD on git ≥ 2.28 and would
        # leave current_branch() returning "", silently satisfying the
        # final assertion for the wrong reason. Force a real HEAD first.
        _git(proj, "config", "user.email", "test@example.com")
        _git(proj, "config", "user.name", "Test User")
        _git(proj, "commit", "--allow-empty", "-m", "init", "--no-gpg-sign")
        _git(proj, "checkout", "-b", "feature/manual")
        db.register_agent("agent-p", proj)
        original = db.insert_message("agent-p", "user", "done", "notify")
        db.record_outbound_email(
            "<manual@x>", kind="notify", sender_agent="agent-p",
        )
        apply_reply(
            db, tq, _StubWM(),
            agent_name="agent-p", original_message_id=original["id"],
            body="also add docs",
            allowed_base=str(tmp_path),
            original_email_message_id="<manual@x>",
        )
        assert _latest(tq)["branch_name"] is None
