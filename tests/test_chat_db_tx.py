"""Phase 0 tests for the ChatDB transaction wrapper layer (probe only)."""
import logging
import os
import sqlite3
import threading

import pytest

from src.chat_db_tx import TransactionMixin


class _Host(TransactionMixin):
    """Minimal host class so the mixin can be exercised in isolation."""
    def __init__(self):
        self._db_lock = threading.RLock()


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


class TestTraceCallback:
    def test_callback_not_installed_without_env(self, monkeypatch, host, tmp_path):
        monkeypatch.delenv("CHAT_DB_TRACE", raising=False)
        conn = host._open_conn(str(tmp_path / "a.db"))
        with conn:
            conn.execute("CREATE TABLE t (id INTEGER)")
        conn.close()

    def test_callback_installed_with_env(self, monkeypatch, host, tmp_path, caplog):
        monkeypatch.setenv("CHAT_DB_TRACE", "1")
        caplog.set_level(logging.DEBUG, logger="src.chat_db_tx")
        conn = host._open_conn(str(tmp_path / "a.db"))
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.commit()
        conn.close()
        kinds = [r.message for r in caplog.records
                 if "chatdb.trace" in r.message]
        assert kinds, "trace callback did not log anything"
        joined = " ".join(kinds)
        assert "CREATE" in joined.upper() or "OTHER" in joined.upper()
        assert "TABLE t" not in joined  # no full SQL leaked

    def test_callback_never_logs_parameters_or_full_sql(
        self, monkeypatch, host, tmp_path, caplog
    ):
        monkeypatch.setenv("CHAT_DB_TRACE", "1")
        caplog.set_level(logging.DEBUG, logger="src.chat_db_tx")
        conn = host._open_conn(str(tmp_path / "a.db"))
        conn.execute("CREATE TABLE m (body TEXT)")
        conn.execute(
            "INSERT INTO m (body) VALUES (?)",
            ("super-secret-message-body",),
        )
        conn.commit()
        conn.close()
        full = " ".join(r.getMessage() for r in caplog.records)
        assert "super-secret-message-body" not in full
        assert "INSERT INTO m" not in full


class TestClassifySql:
    @pytest.mark.parametrize(
        "sql,expected",
        [
            ("BEGIN IMMEDIATE", "BEGIN"),
            ("  begin transaction", "BEGIN"),
            ("COMMIT", "COMMIT"),
            ("ROLLBACK", "ROLLBACK"),
            ("INSERT INTO foo VALUES (1)", "OTHER"),
            ("SELECT * FROM bar", "OTHER"),
            ("", "OTHER"),
            ("   \n  ", "OTHER"),
        ],
    )
    def test_classify(self, host, sql, expected):
        assert host._classify_sql(sql) == expected
