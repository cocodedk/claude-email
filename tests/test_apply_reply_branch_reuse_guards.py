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


class TestGuards:
    def test_null_sender_agent_rejects_prior(self, db, tq, tmp_path):
        proj = _project_dir(tmp_path)
        db.register_agent("agent-p", proj)
        prior_id = tq.enqueue(
            proj, "implement X",
            branch_name="claude/task-9-foo", mutates_repo=True,
            origin_message_id="<orig@x>", origin_from="user@example.org",
        )
        tq.mark_done(prior_id)
        db._conn.execute(
            "INSERT INTO outbound_emails "
            "(email_message_id, sent_at, kind, sender_agent, task_id) "
            "VALUES (?, ?, ?, NULL, ?)",
            ("<no-sender@x>", "2026-05-17T00:00:00+00:00", "result", prior_id),
        )
        db._conn.commit()
        original = db.insert_message("agent-p", "user", "done", "notify")
        apply_reply(
            db, tq, _StubWM(),
            agent_name="agent-p", original_message_id=original["id"],
            body="follow up",
            allowed_base=str(tmp_path),
            original_email_message_id="<no-sender@x>",
        )
        assert _latest(tq)["branch_name"] is None

    def test_project_mismatch_rejects_prior(self, db, tq, tmp_path):
        proj_a = _project_dir(tmp_path, "a")
        proj_b = _project_dir(tmp_path, "b")
        db.register_agent("agent-a", proj_a)
        db.register_agent("agent-b", proj_b)
        prior_id, out_id = _seed_prior_task(
            db, tq, proj_a, "claude/task-1-thing-in-a",
            agent_name="agent-a", mutating=True,
        )
        original = db.insert_message("agent-b", "user", "done", "notify")
        apply_reply(
            db, tq, _StubWM(),
            agent_name="agent-b", original_message_id=original["id"],
            body="follow up",
            allowed_base=str(tmp_path),
            original_email_message_id=out_id,
        )
        new = _latest(tq)
        assert new["project_path"] == proj_b
        assert new["branch_name"] is None

    def test_agent_mismatch_rejects_prior(self, db, tq, tmp_path):
        proj = _project_dir(tmp_path)
        db.register_agent("agent-p", proj)
        db.register_agent("agent-other", proj)
        prior_id, out_id = _seed_prior_task(
            db, tq, proj, "claude/task-9-foo",
            agent_name="agent-other", mutating=True,
        )
        original = db.insert_message("agent-p", "user", "done", "notify")
        apply_reply(
            db, tq, _StubWM(),
            agent_name="agent-p", original_message_id=original["id"],
            body="follow up",
            allowed_base=str(tmp_path),
            original_email_message_id=out_id,
        )
        new = _latest(tq)
        assert new["branch_name"] is None

    def test_same_agent_but_prior_task_in_different_project_rejects(
        self, db, tq, tmp_path,
    ):
        """Sender_agent matches, but prior task's project_path differs
        from the reply agent's current project — the project-path guard
        must still reject the branch."""
        proj_a = _project_dir(tmp_path, "a")
        proj_b = _project_dir(tmp_path, "b")
        # Agent moved projects between the prior task and the reply:
        # outbound row records agent-mover sending for proj_a, but the
        # agent is now registered against proj_b.
        prior_id, out_id = _seed_prior_task(
            db, tq, proj_a, "claude/task-7-thing-in-a",
            agent_name="agent-mover", mutating=True,
        )
        db.register_agent("agent-mover", proj_b)
        original = db.insert_message(
            "agent-mover", "user", "done", "notify",
        )
        apply_reply(
            db, tq, _StubWM(),
            agent_name="agent-mover", original_message_id=original["id"],
            body="follow up",
            allowed_base=str(tmp_path),
            original_email_message_id=out_id,
        )
        new = _latest(tq)
        assert new["project_path"] == proj_b
        assert new["branch_name"] is None

    def test_malformed_prior_branch_name_rejects(self, db, tq, tmp_path):
        """A prior task row whose branch_name doesn't match the
        claude/task-<id>-<slug> schema must be discarded. The ACK can't
        promise 'continue prior branch <garbage>' while branch_prep
        silently falls back to a fresh branch."""
        proj = _project_dir(tmp_path)
        db.register_agent("agent-p", proj)
        prior_id, out_id = _seed_prior_task(
            db, tq, proj, "not-a-task-branch",
            agent_name="agent-p", mutating=True,
        )
        original = db.insert_message("agent-p", "user", "done", "notify")
        apply_reply(
            db, tq, _StubWM(),
            agent_name="agent-p", original_message_id=original["id"],
            body="follow up",
            allowed_base=str(tmp_path),
            original_email_message_id=out_id,
        )
        new = _latest(tq)
        assert new["branch_name"] is None

    def test_outbound_points_to_missing_task_rejects(
        self, db, tq, tmp_path,
    ):
        """Outbound row matches and sender_agent matches, but the
        prior task row is gone (FK survived an offline ALTER, manual
        DBA repair, etc.) — the lookup fails closed."""
        proj = _project_dir(tmp_path)
        db.register_agent("agent-p", proj)
        prior_id, out_id = _seed_prior_task(
            db, tq, proj, "claude/task-77-old", mutating=True,
        )
        # Drop the prior task without violating the outbound FK.
        db._conn.execute("PRAGMA foreign_keys=OFF")
        db._conn.execute("DELETE FROM tasks WHERE id=?", (prior_id,))
        db._conn.commit()
        db._conn.execute("PRAGMA foreign_keys=ON")
        original = db.insert_message("agent-p", "user", "done", "notify")
        apply_reply(
            db, tq, _StubWM(),
            agent_name="agent-p", original_message_id=original["id"],
            body="follow up",
            allowed_base=str(tmp_path),
            original_email_message_id=out_id,
        )
        assert _latest(tq)["branch_name"] is None
