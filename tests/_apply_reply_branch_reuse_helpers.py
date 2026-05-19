"""Shared helpers for tests/test_apply_reply_branch_reuse_*.py.

Underscore-prefixed so pytest skips collection. Holds the non-fixture
helpers (stubs, factories, query helpers) used by every split file.
The pytest fixtures themselves (db_path, db, tq) live in each test
file because pytest only auto-discovers fixtures from test modules
and conftest.py — and conftest is off-limits for this split."""
from src.chat_db import ChatDB
from src.task_queue import TaskQueue


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
