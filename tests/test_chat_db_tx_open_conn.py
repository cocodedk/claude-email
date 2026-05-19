"""Tests for the ChatDB transaction wrapper layer."""
import sqlite3
import threading

import pytest

from src.chat_db_tx import TransactionMixin


class _Host(TransactionMixin):
    """Minimal host class so the mixin can be exercised in isolation."""
    def __init__(self):
        self._init_db_lock()


@pytest.fixture
def host():
    return _Host()


class TestOpenConn:
    def test_returns_a_connection(self, host, tmp_path):
        conn = host._open_conn(str(tmp_path / "a.db"))
        assert isinstance(conn, sqlite3.Connection)
        conn.close()

    def test_row_factory_is_row(self, host, tmp_path):
        conn = host._open_conn(str(tmp_path / "a.db"))
        assert conn.row_factory is sqlite3.Row
        conn.close()

    def test_wal_mode_enabled(self, host, tmp_path):
        conn = host._open_conn(str(tmp_path / "a.db"))
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
        conn.close()

    def test_busy_timeout_is_200ms(self, host, tmp_path):
        conn = host._open_conn(str(tmp_path / "a.db"))
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 200
        conn.close()

    def test_foreign_keys_enabled(self, host, tmp_path):
        conn = host._open_conn(str(tmp_path / "a.db"))
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        conn.close()

    def test_check_same_thread_off(self, host, tmp_path):
        conn = host._open_conn(str(tmp_path / "a.db"))
        # sqlite3 doesn't expose check_same_thread on the conn directly,
        # so verify by using the conn from another thread without raising.
        errors = []
        def run():
            try:
                conn.execute("SELECT 1").fetchone()
            except Exception as e:
                errors.append(e)
        t = threading.Thread(target=run)
        t.start()
        t.join()
        assert errors == []
        conn.close()
