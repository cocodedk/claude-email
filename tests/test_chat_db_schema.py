"""Tests for the shared SQLite database layer (ChatDB) — schema/pragmas."""
import sqlite3
import pytest
from src.chat_db import ChatDB


@pytest.fixture
def db(tmp_path):
    return ChatDB(str(tmp_path / "test.db"))


class TestSchema:
    def test_wal_mode_enabled(self, db):
        cur = db._conn.execute("PRAGMA journal_mode")
        assert cur.fetchone()[0] == "wal"

    def test_busy_timeout_set(self, db):
        cur = db._conn.execute("PRAGMA busy_timeout")
        # 200 ms per spec §6 — bounds event-loop block; retry handles
        # genuine cross-process contention.
        assert cur.fetchone()[0] == 200

    def test_foreign_keys_enabled(self, db):
        cur = db._conn.execute("PRAGMA foreign_keys")
        assert cur.fetchone()[0] == 1

    def test_row_factory_is_row(self, db):
        assert db._conn.row_factory == sqlite3.Row

    def test_tables_exist(self, db):
        cur = db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        names = [r[0] for r in cur.fetchall()]
        assert "agents" in names
        assert "events" in names
        assert "messages" in names

    def test_reopen_existing_db(self, tmp_path):
        path = str(tmp_path / "reopen.db")
        db1 = ChatDB(path)
        db1.register_agent("a1", "/tmp/a1")
        db2 = ChatDB(path)
        assert db2.get_agent("a1") is not None
