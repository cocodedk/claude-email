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


class TestStatusCodes:
    def test_two_codes_locked(self):
        assert STATUS_CODES == {"stalled", "waiting-on-peer"}

    def test_unknown_code_raises(self, cdb, tq):
        tid = tq.enqueue("/p", "x")
        with pytest.raises(ValueError, match="unknown status"):
            emit_status(cdb, tid, "working")


class TestEmitStatus:
    def _last_msg_body(self, cdb):
        row = cdb._conn.execute(
            "SELECT body FROM messages ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return json.loads(row["body"])

    def test_first_emission_inserts_message(self, cdb, tq):
        tid = tq.enqueue("/p", "x")
        assert emit_status(cdb, tid, "waiting-on-peer") is True
        pending = cdb.get_pending_messages_for("user")
        assert len(pending) == 1
        assert pending[0]["content_type"] == "application/json"
        assert pending[0]["task_id"] == tid
        assert pending[0]["type"] == "notify"
        body = self._last_msg_body(cdb)
        assert body["kind"] == "status"
        assert body["task_id"] == tid
        assert body["data"]["status"] == "waiting-on-peer"

    def test_from_name_derived_from_project_basename(self, cdb, tq):
        tid = tq.enqueue("/home/u/projects/my-proj", "x")
        emit_status(cdb, tid, "stalled")
        pending = cdb.get_pending_messages_for("user")
        assert pending[0]["from_name"] == "agent-my-proj"

    def test_dedup_same_status_second_call_returns_false(self, cdb, tq):
        tid = tq.enqueue("/p", "x")
        emit_status(cdb, tid, "stalled")
        assert emit_status(cdb, tid, "stalled") is False
        pending = cdb.get_pending_messages_for("user")
        assert len(pending) == 1  # not duplicated

    def test_transition_emits_again(self, cdb, tq):
        tid = tq.enqueue("/p", "x")
        emit_status(cdb, tid, "stalled")
        assert emit_status(cdb, tid, "waiting-on-peer") is True
        pending = cdb.get_pending_messages_for("user")
        assert len(pending) == 2

    def test_reason_threaded_through_when_set(self, cdb, tq):
        tid = tq.enqueue("/p", "x")
        emit_status(
            cdb, tid, "waiting-on-peer", reason="plan awaiting approval",
        )
        body = self._last_msg_body(cdb)
        assert body["data"]["reason"] == "plan awaiting approval"

    def test_reason_omitted_when_empty(self, cdb, tq):
        tid = tq.enqueue("/p", "x")
        emit_status(cdb, tid, "stalled")
        body = self._last_msg_body(cdb)
        assert "reason" not in body["data"]

    def test_retry_after_seconds_on_stalled(self, cdb, tq):
        tid = tq.enqueue("/p", "x")
        emit_status(cdb, tid, "stalled", retry_after_seconds=42)
        body = self._last_msg_body(cdb)
        assert body["data"]["retry_after_seconds"] == 42

    def test_retry_after_seconds_ignored_on_waiting_on_peer(self, cdb, tq):
        """retry_after_seconds is stalled-only per spec — silently dropped
        on other states to keep the envelope clean."""
        tid = tq.enqueue("/p", "x")
        emit_status(cdb, tid, "waiting-on-peer", retry_after_seconds=42)
        body = self._last_msg_body(cdb)
        assert "retry_after_seconds" not in body["data"]

    def test_last_activity_at_threaded_when_set(self, cdb, tq):
        tid = tq.enqueue("/p", "x")
        emit_status(cdb, tid, "stalled", last_activity_at="2026-04-24T08:00:00+00:00")
        body = self._last_msg_body(cdb)
        assert body["data"]["last_activity_at"] == "2026-04-24T08:00:00+00:00"

    def test_nonexistent_task_returns_false_no_insert(self, cdb):
        assert emit_status(cdb, 9999, "stalled") is False
        assert cdb.get_pending_messages_for("user") == []

    def test_last_sent_status_persisted(self, cdb, tq):
        tid = tq.enqueue("/p", "x")
        emit_status(cdb, tid, "stalled")
        row = cdb._conn.execute(
            "SELECT last_sent_status FROM tasks WHERE id=?", (tid,)
        ).fetchone()
        assert row["last_sent_status"] == "stalled"

    def test_dedup_mark_persists_even_if_insert_raises(self, cdb, tq, mocker):
        """If insert_message raises after the dedup UPDATE commits, the
        next call must dedup into a silent no-op rather than double-emit
        on the next tick."""
        tid = tq.enqueue("/p", "x")
        mocker.patch.object(cdb, "insert_message", side_effect=RuntimeError("smtp-like blip"))
        with pytest.raises(RuntimeError):
            emit_status(cdb, tid, "stalled")
        # dedup mark survived the insert failure
        row = cdb._conn.execute(
            "SELECT last_sent_status FROM tasks WHERE id=?", (tid,)
        ).fetchone()
        assert row["last_sent_status"] == "stalled"
        # next call dedupes silently — no second insert attempt, no re-raise
        mocker.stopall()
        assert emit_status(cdb, tid, "stalled") is False
        assert cdb.get_pending_messages_for("user") == []


class TestEmitStalledForProject:
    def test_emits_on_running_task(self, cdb, tq):
        tid = tq.enqueue("/p", "x")
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

    def test_swallows_db_errors(self, cdb):
        """emit_stalled_for_project must never raise into wake_watcher —
        a DB blip at the wrong moment cannot break the wake loop."""
        from unittest.mock import MagicMock
        from src.status_envelope import emit_stalled_for_project
        cdb._conn = MagicMock(side_effect=RuntimeError("db blip"))
        cdb._conn.execute.side_effect = RuntimeError("db blip")
        assert emit_stalled_for_project(cdb, "/p") is False
