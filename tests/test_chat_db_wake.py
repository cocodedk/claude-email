"""Tests for wake_sessions schema + ChatDB wake methods."""
import os
import tempfile

import pytest

from src.chat_db import ChatDB


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        yield ChatDB(path)
    finally:
        os.unlink(path)


def test_wake_sessions_table_exists(db):
    cur = db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='wake_sessions'"
    )
    assert cur.fetchone() is not None


def test_wake_sessions_columns(db):
    rows = db._conn.execute("PRAGMA table_info(wake_sessions)").fetchall()
    cols = {r["name"] for r in rows}
    assert cols == {"agent_name", "session_id", "last_turn_at"}


def test_get_wake_session_missing(db):
    assert db.get_wake_session("agent-foo") is None


def test_get_wake_session_present(db):
    db._conn.execute(
        "INSERT INTO wake_sessions VALUES ('agent-foo','uuid-1','2026-04-20T00:00:00Z')"
    )
    db._conn.commit()
    row = db.get_wake_session("agent-foo")
    assert row["session_id"] == "uuid-1"
    assert row["last_turn_at"] == "2026-04-20T00:00:00Z"


def test_upsert_wake_session_insert(db):
    db.upsert_wake_session("agent-foo", "uuid-1")
    row = db.get_wake_session("agent-foo")
    assert row["session_id"] == "uuid-1"
    assert row["last_turn_at"]


def test_upsert_wake_session_update_bumps_timestamp(db):
    db.upsert_wake_session("agent-foo", "uuid-1")
    first = db.get_wake_session("agent-foo")["last_turn_at"]
    db.upsert_wake_session("agent-foo", "uuid-2")
    row = db.get_wake_session("agent-foo")
    assert row["session_id"] == "uuid-2"
    assert row["last_turn_at"] >= first


def test_delete_wake_session_removes_row(db):
    db.upsert_wake_session("agent-foo", "uuid-1")
    db.delete_wake_session("agent-foo")
    assert db.get_wake_session("agent-foo") is None


def test_delete_wake_session_noop_on_missing(db):
    db.delete_wake_session("agent-nope")
