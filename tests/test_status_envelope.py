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
        tid = tq.enqueue("/p", "x", origin_content_type="application/json")
        with pytest.raises(ValueError, match="unknown status"):
            emit_status(cdb, tid, "working")


class TestEmitStatus:
    def _last_msg_body(self, cdb):
        row = cdb._conn.execute(
            "SELECT body FROM messages ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return json.loads(row["body"])

    def test_first_emission_inserts_message(self, cdb, tq):
        tid = tq.enqueue("/p", "x", origin_content_type="application/json")
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
        tid = tq.enqueue(
            "/home/u/projects/my-proj", "x",
            origin_content_type="application/json",
        )
        emit_status(cdb, tid, "stalled")
        pending = cdb.get_pending_messages_for("user")
        assert pending[0]["from_name"] == "agent-my-proj"

    def test_dedup_same_status_second_call_returns_false(self, cdb, tq):
        tid = tq.enqueue("/p", "x", origin_content_type="application/json")
        emit_status(cdb, tid, "stalled")
        assert emit_status(cdb, tid, "stalled") is False
        pending = cdb.get_pending_messages_for("user")
        assert len(pending) == 1  # not duplicated

    def test_transition_emits_again(self, cdb, tq):
        tid = tq.enqueue("/p", "x", origin_content_type="application/json")
        emit_status(cdb, tid, "stalled")
        assert emit_status(cdb, tid, "waiting-on-peer") is True
        pending = cdb.get_pending_messages_for("user")
        assert len(pending) == 2

    def test_reason_threaded_through_when_set(self, cdb, tq):
        tid = tq.enqueue("/p", "x", origin_content_type="application/json")
        emit_status(
            cdb, tid, "waiting-on-peer", reason="plan awaiting approval",
        )
        body = self._last_msg_body(cdb)
        assert body["data"]["reason"] == "plan awaiting approval"

    def test_reason_omitted_when_empty(self, cdb, tq):
        tid = tq.enqueue("/p", "x", origin_content_type="application/json")
        emit_status(cdb, tid, "stalled")
        body = self._last_msg_body(cdb)
        assert "reason" not in body["data"]

    def test_retry_after_seconds_on_stalled(self, cdb, tq):
        tid = tq.enqueue("/p", "x", origin_content_type="application/json")
        emit_status(cdb, tid, "stalled", retry_after_seconds=42)
        body = self._last_msg_body(cdb)
        assert body["data"]["retry_after_seconds"] == 42

    def test_retry_after_seconds_ignored_on_waiting_on_peer(self, cdb, tq):
        """retry_after_seconds is stalled-only per spec — silently dropped
        on other states to keep the envelope clean."""
        tid = tq.enqueue("/p", "x", origin_content_type="application/json")
        emit_status(cdb, tid, "waiting-on-peer", retry_after_seconds=42)
        body = self._last_msg_body(cdb)
        assert "retry_after_seconds" not in body["data"]

    def test_last_activity_at_threaded_when_set(self, cdb, tq):
        tid = tq.enqueue("/p", "x", origin_content_type="application/json")
        emit_status(cdb, tid, "stalled", last_activity_at="2026-04-24T08:00:00+00:00")
        body = self._last_msg_body(cdb)
        assert body["data"]["last_activity_at"] == "2026-04-24T08:00:00+00:00"

    def test_nonexistent_task_returns_false_no_insert(self, cdb):
        assert emit_status(cdb, 9999, "stalled") is False
        assert cdb.get_pending_messages_for("user") == []

    def test_last_sent_status_persisted(self, cdb, tq):
        tid = tq.enqueue("/p", "x", origin_content_type="application/json")
        emit_status(cdb, tid, "stalled")
        row = cdb._conn.execute(
            "SELECT last_sent_status FROM tasks WHERE id=?", (tid,)
        ).fetchone()
        assert row["last_sent_status"] == "stalled"

    def test_dedup_mark_persists_even_if_insert_raises(self, cdb, tq, mocker):
        """If insert_message raises after the dedup UPDATE commits, the
        next call must dedup into a silent no-op rather than double-emit
        on the next tick."""
        tid = tq.enqueue("/p", "x", origin_content_type="application/json")
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
