"""Tests for the branch-reuse + guards path in src/reply_router.apply_reply.

Lookup chain: In-Reply-To → outbound_emails.task_id → tasks row →
branch_name + mutates_repo. Guards: project_path must match, and
outbound.sender_agent must match agent_name."""
import pytest

from src.chat_db import ChatDB
from src.git_ops import current_branch
from src.reply_router import apply_reply
from src.task_queue import TaskQueue


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


class _StubWM:
    def __init__(self, pid=111):
        self.pid = pid

    def ensure_worker(self, _path):
        return self.pid


def _project_dir(tmp_path, name="p"):
    p = tmp_path / name
    p.mkdir()
    return str(p.resolve())


def _seed_prior_task(
    db, tq, project_path, branch_name, agent_name="agent-p", mutating=True,
):
    """Insert a completed prior task + a relayed outbound email pointing
    to it. Returns (task_id, outbound Message-ID)."""
    tid = tq.enqueue(
        project_path, "implement X",
        branch_name=branch_name,
        mutates_repo=mutating,
        origin_message_id="<orig@x>",
        origin_from="user@example.org",
    )
    tq.mark_done(tid)
    db.insert_message(agent_name, "user", "done", "notify", task_id=tid)
    out_id = f"<sent-{tid}@x>"
    db.record_outbound_email(
        out_id, kind="result", sender_agent=agent_name, task_id=tid,
    )
    return tid, out_id


def _latest(tq):
    """Return the most recently inserted task across all projects."""
    row = tq._conn.execute(
        "SELECT * FROM tasks ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return dict(row)


def _git(repo, *args):
    import subprocess
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


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
