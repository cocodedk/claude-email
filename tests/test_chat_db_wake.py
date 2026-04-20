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
