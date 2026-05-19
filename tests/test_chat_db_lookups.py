"""Tests for the shared SQLite database layer (ChatDB) — read-helper lookups."""
import pytest
from src.chat_db import ChatDB


@pytest.fixture
def db(tmp_path):
    return ChatDB(str(tmp_path / "test.db"))


class TestLookupHelpers:
    """Unit tests for relay / routing / envelope ChatDB read helpers."""

    def _seed_task(self, db, **kwargs):
        from src.task_queue import TaskQueue
        tq = TaskQueue(db.path)
        return tq.enqueue("/proj", "body", **kwargs)

    def test_lookup_task_origin_message_id_found(self, db):
        tid = self._seed_task(db, origin_message_id="<mid@example.com>")
        row = db.lookup_task_origin_message_id(tid)
        assert row is not None
        assert row["origin_message_id"] == "<mid@example.com>"

    def test_lookup_task_origin_message_id_missing(self, db):
        assert db.lookup_task_origin_message_id(99999) is None

    def test_lookup_task_origin_message_id_null_when_not_set(self, db):
        tid = self._seed_task(db)
        row = db.lookup_task_origin_message_id(tid)
        assert row is not None
        assert row["origin_message_id"] is None

    def test_lookup_user_to_agent_message_found(self, db):
        db.register_agent("bot", "/p")
        db.insert_message("user", "bot", "hi", "chat")
        row = db.lookup_user_to_agent_message("bot")
        assert row is not None

    def test_lookup_user_to_agent_message_not_found(self, db):
        assert db.lookup_user_to_agent_message("ghost") is None

    def test_lookup_origin_envelope_version_found(self, db):
        tid = self._seed_task(
            db,
            origin_content_type="application/json",
            origin_envelope_v=2,
        )
        row = db.lookup_origin_envelope_version(tid)
        assert row is not None
        assert row["origin_content_type"] == "application/json"
        assert row["origin_envelope_v"] == 2

    def test_lookup_origin_envelope_version_missing(self, db):
        assert db.lookup_origin_envelope_version(99999) is None

    def test_lookup_task_origin_subject_found(self, db):
        tid = self._seed_task(db, origin_subject="Re: hello")
        row = db.lookup_task_origin_subject(tid)
        assert row is not None
        assert row["origin_subject"] == "Re: hello"

    def test_lookup_task_origin_subject_missing(self, db):
        assert db.lookup_task_origin_subject(99999) is None

    def test_lookup_task_routing_found_with_origin_from(self, db):
        tid = self._seed_task(db, origin_from="alice@example.com")
        row = db.lookup_task_routing(tid)
        assert row is not None
        assert row["origin_from"] == "alice@example.com"
        assert row["project_path"] == "/proj"

    def test_lookup_task_routing_missing(self, db):
        assert db.lookup_task_routing(99999) is None

    def test_lookup_task_routing_null_origin_from(self, db):
        tid = self._seed_task(db)
        row = db.lookup_task_routing(tid)
        assert row is not None
        assert row["origin_from"] is None
        assert row["project_path"] == "/proj"

    def test_lookup_task_status_info_found(self, db):
        tid = self._seed_task(
            db,
            origin_content_type="application/json",
            origin_envelope_v=3,
        )
        row = db.lookup_task_status_info(tid)
        assert row is not None
        assert row["project_path"] == "/proj"
        assert row["origin_content_type"] == "application/json"
        assert row["origin_envelope_v"] == 3

    def test_lookup_task_status_info_missing(self, db):
        assert db.lookup_task_status_info(99999) is None
