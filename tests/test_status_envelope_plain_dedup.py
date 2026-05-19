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


class TestPlainTextOrigin:
    """Tasks that originated from plain-text email must NOT receive a JSON
    envelope status — that would arrive as raw JSON in a generic mail
    client. Mirrors notify_task_done's content-type handling."""

    def test_plain_origin_skips_json_envelope(self, cdb, tq):
        tid = tq.enqueue("/p", "x")  # default origin → text/plain
        assert emit_status(cdb, tid, "stalled", reason="no heartbeat") is True
        pending = cdb.get_pending_messages_for("user")
        assert len(pending) == 1
        assert pending[0]["content_type"] in (None, "")
        assert pending[0]["body"].startswith("Task #")
        assert "stalled" in pending[0]["body"]
        assert "Reason: no heartbeat" in pending[0]["body"]

    def test_plain_origin_includes_retry_after(self, cdb, tq):
        tid = tq.enqueue("/p", "x")
        emit_status(cdb, tid, "stalled", retry_after_seconds=42)
        pending = cdb.get_pending_messages_for("user")
        assert "Retry after: 42s" in pending[0]["body"]

    def test_plain_origin_dedup_still_works(self, cdb, tq):
        tid = tq.enqueue("/p", "x")
        emit_status(cdb, tid, "stalled")
        assert emit_status(cdb, tid, "stalled") is False
        assert len(cdb.get_pending_messages_for("user")) == 1

    def test_plain_origin_includes_last_activity_at(self, cdb, tq):
        tid = tq.enqueue("/p", "x")
        emit_status(cdb, tid, "stalled", last_activity_at="2026-04-25T07:00:00+00:00")
        pending = cdb.get_pending_messages_for("user")
        assert "2026-04-25T07:00:00+00:00" in pending[0]["body"]


class TestClearStatusDedup:
    """Episode-scoped dedup: once a state ends (ask got reply, wake
    delivered progress), the marker must clear so the next entry into
    that state emits a fresh envelope. Otherwise repeated chat_ask calls
    or recovered-then-stalled tasks go silent on the bus."""

    def test_clear_lets_same_status_re_emit(self, cdb, tq):
        from src.status_envelope import clear_status_dedup
        tid = tq.enqueue("/p", "x", origin_content_type="application/json")
        emit_status(cdb, tid, "waiting-on-peer")
        clear_status_dedup(cdb, tid)
        assert emit_status(cdb, tid, "waiting-on-peer") is True
        assert len(cdb.get_pending_messages_for("user")) == 2

    def test_clear_unknown_task_silent_no_op(self, cdb):
        from src.status_envelope import clear_status_dedup
        clear_status_dedup(cdb, 999_999)  # must not raise

    def test_clear_for_project_targets_running_task(self, cdb, tq):
        from src.status_envelope import clear_status_dedup_for_project
        tid = tq.enqueue("/p", "x", origin_content_type="application/json")
        tq.claim_next("/p")
        emit_status(cdb, tid, "stalled")
        clear_status_dedup_for_project(cdb, "/p")
        row = cdb._conn.execute(
            "SELECT last_sent_status FROM tasks WHERE id=?", (tid,)
        ).fetchone()
        assert row["last_sent_status"] is None

    def test_clear_for_project_skips_terminal_tasks(self, cdb, tq):
        """Only the running task gets cleared — done/failed tasks keep
        their final marker (they're not coming back)."""
        from src.status_envelope import clear_status_dedup_for_project
        terminal = tq.enqueue("/p", "x", origin_content_type="application/json")
        tq.claim_next("/p")
        emit_status(cdb, terminal, "stalled")
        tq.mark_done(terminal)
        clear_status_dedup_for_project(cdb, "/p")
        row = cdb._conn.execute(
            "SELECT last_sent_status FROM tasks WHERE id=?", (terminal,)
        ).fetchone()
        assert row["last_sent_status"] == "stalled"
