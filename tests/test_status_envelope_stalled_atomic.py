"""Tests for src/status_envelope.py — kind=status emitter + dedup."""
import json

import pytest
from src.chat_db import ChatDB
from src.status_envelope import STATUS_CODES, emit_status
from src.task_queue import TaskQueue


@pytest.fixture
def db_path(tmp_path):
    p = str(tmp_path / "db")
    ChatDB(p)
    return p


@pytest.fixture
def cdb(db_path):
    return ChatDB(db_path)


@pytest.fixture
def tq(db_path):
    return TaskQueue(db_path)


class TestEmitStalledForProject:
    def test_emits_on_running_task(self, cdb, tq):
        tid = tq.enqueue("/p", "x", origin_content_type="application/json")
        tq.claim_next("/p")  # flip to running
        from src.status_envelope import emit_stalled_for_project
        assert emit_stalled_for_project(cdb, "/p", reason="boom") is True
        row = cdb._conn.execute(
            "SELECT last_sent_status FROM tasks WHERE id=?", (tid,)
        ).fetchone()
        assert row["last_sent_status"] == "stalled"

    def test_noop_when_no_running_task(self, cdb, tq):
        from src.status_envelope import emit_stalled_for_project
        assert emit_stalled_for_project(cdb, "/nowhere") is False

    def test_swallows_db_errors(self, cdb, mocker):
        """emit_stalled_for_project must never raise into wake_watcher —
        a DB blip at the wrong moment cannot break the wake loop. We
        force the inner TaskQueue.get_running to raise so the bare
        ``except`` branch (the actual safety net) executes."""
        from src.status_envelope import emit_stalled_for_project
        mocker.patch(
            "src.status_envelope.TaskQueue.get_running",
            side_effect=RuntimeError("db blip"),
        )
        assert emit_stalled_for_project(cdb, "/p") is False


def test_emit_status_atomic_on_insert_failure(tmp_path, mocker):
    """If _impl_emit_status_message raises during the INSERT, the tx
    rolls back and last_sent_status is NOT advanced — closing the
    previous silent-drop window where the UPDATE committed first."""
    from src.chat_db import ChatDB
    from src.status_envelope import emit_status

    db = ChatDB(str(tmp_path / "a.db"))
    db._conn.execute(
        "INSERT INTO tasks (id, project_path, body, created_at, last_sent_status) "
        "VALUES (1, '/p', 'b', '2026-05-19T00:00:00+00:00', NULL)"
    )
    db._conn.commit()

    real_impl = db._impl_emit_status_message

    def fail_on_insert(task_id, status, agent_name, body, content_type):
        # advance the UPDATE, then blow up before RETURNING
        db._conn.execute(
            "UPDATE tasks SET last_sent_status=? WHERE id=?",
            (status, task_id),
        )
        raise RuntimeError("forced insert failure")

    mocker.patch.object(db, "_impl_emit_status_message", side_effect=fail_on_insert)
    with pytest.raises(RuntimeError):
        emit_status(db, 1, "waiting-on-peer")

    # _run_tx rolled back — last_sent_status was never advanced
    mocker.stopall()
    row = db._conn.execute(
        "SELECT last_sent_status FROM tasks WHERE id=1"
    ).fetchone()
    assert row["last_sent_status"] is None
