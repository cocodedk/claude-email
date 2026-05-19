"""Tests for the ChatDB transaction wrapper layer."""
import logging
import sqlite3

import pytest

from src.chat_db_tx import TransactionMixin


class _Host(TransactionMixin):
    """Minimal host class so the mixin can be exercised in isolation."""
    def __init__(self):
        self._init_db_lock()


@pytest.fixture
def host():
    return _Host()


class TestTraceCallback:
    def test_callback_not_installed_without_env(self, monkeypatch, host, tmp_path, caplog):
        monkeypatch.delenv("CHAT_DB_TRACE", raising=False)
        caplog.set_level(logging.DEBUG, logger="src.chat_db_tx")
        conn = host._open_conn(str(tmp_path / "a.db"))
        with conn:
            conn.execute("CREATE TABLE t (id INTEGER)")
        conn.close()
        trace_lines = [r for r in caplog.records
                       if "chatdb.trace" in r.getMessage()]
        assert trace_lines == [], (
            "trace callback fired without CHAT_DB_TRACE=1 — env guard regressed"
        )

    def test_callback_installed_with_env(self, monkeypatch, host, tmp_path, caplog):
        monkeypatch.setenv("CHAT_DB_TRACE", "1")
        caplog.set_level(logging.DEBUG, logger="src.chat_db_tx")
        conn = host._open_conn(str(tmp_path / "a.db"))
        host._conn = conn  # mirror ChatDB.__init__ so _trace_cb sees the live conn
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
        host._conn = conn
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

    def test_callback_reports_in_transaction_state(
        self, monkeypatch, host, tmp_path, caplog
    ):
        """When the host's _conn is set, _trace_cb's in_transaction field
        reflects the live connection state for downstream callers to
        recognise stale transactions via the trace output."""
        monkeypatch.setenv("CHAT_DB_TRACE", "1")
        caplog.set_level(logging.DEBUG, logger="src.chat_db_tx")
        conn = host._open_conn(str(tmp_path / "a.db"))
        host._conn = conn
        try:
            conn.execute("CREATE TABLE t (id INTEGER)")
            conn.execute("BEGIN")
            # DML inside the explicit transaction — in_transaction is True
            # by the time SQLite invokes the trace callback for INSERT.
            conn.execute("INSERT INTO t VALUES (1)")
            inside = [r.getMessage() for r in caplog.records
                      if "chatdb.trace" in r.getMessage()
                      and "in_transaction=True" in r.getMessage()]
            assert inside, "expected at least one trace line with in_transaction=True"
        finally:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            conn.close()
