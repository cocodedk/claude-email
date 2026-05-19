"""Tests for the ChatDB transaction wrapper layer."""
import logging
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
            # SQLite trace_v2 hands the callback None on some builds —
            # the `or ""` guard protects against that and this case pins
            # the contract so a future cleanup can't silently drop it.
            (None, "OTHER"),
        ],
    )
    def test_classify(self, sql, expected):
        assert TransactionMixin._classify_sql(sql) == expected


class TestDbLock:
    def test_init_db_lock_attaches_rlock(self):
        class H(TransactionMixin):
            pass
        h = H()
        h._init_db_lock()
        # RLock is internally `threading._RLock`; verify by re-acquiring
        # from the same thread (would deadlock on a plain Lock).
        with h._db_lock:
            with h._db_lock:
                pass

    def test_init_db_lock_is_idempotent(self):
        class H(TransactionMixin):
            pass
        h = H()
        h._init_db_lock()
        first = h._db_lock
        h._init_db_lock()
        # Idempotent: second call does NOT replace the lock (would lose
        # any acquisition state). Same object identity expected.
        assert h._db_lock is first


class TestPoisonRecovery:
    def test_clean_conn_no_op(self, host, tmp_path):
        conn = host._open_conn(str(tmp_path / "a.db"))
        host._conn = conn
        host._check_or_recover_at_depth_zero("test_method")
        # No exception; connection unchanged.
        assert host._conn is conn
        conn.close()

    def test_stale_tx_rolled_back(self, host, tmp_path, caplog):
        import logging as _logging
        caplog.set_level(_logging.WARNING, logger="src.chat_db_tx")
        conn = host._open_conn(str(tmp_path / "a.db"))
        host._conn = conn
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.execute("BEGIN")
        conn.execute("INSERT INTO t VALUES (1)")
        assert conn.in_transaction is True
        host._check_or_recover_at_depth_zero("test_method")
        assert conn.in_transaction is False
        warnings = [r for r in caplog.records
                    if "chatdb.lock_event" in r.getMessage()
                    and "kind=stale_tx" in r.getMessage()
                    and "test_method" in r.getMessage()]
        assert warnings, "expected one stale_tx warning"
        conn.close()

    def test_rollback_failure_swaps_connection(
        self, host, tmp_path, caplog,
    ):
        import logging as _logging

        class _BrokenConn:
            """Stub that looks poisoned (in_transaction=True) and has a
            rollback that raises, so _close_and_reopen gets triggered."""
            in_transaction = True
            closed = False

            def rollback(self):
                raise sqlite3.OperationalError("forced")

            def close(self):
                self.closed = True

        caplog.set_level(_logging.WARNING, logger="src.chat_db_tx")
        broken = _BrokenConn()
        host._conn = broken
        host.path = str(tmp_path / "a.db")
        host._check_or_recover_at_depth_zero("test_method")
        # Connection was replaced — host._conn is a new object.
        assert host._conn is not broken
        assert broken.closed  # best-effort close was called
        replaced_warnings = [r for r in caplog.records
                             if "kind=rollback_failed" in r.getMessage()]
        assert replaced_warnings
        host._conn.close()

    def test_close_failure_during_recovery_is_logged(
        self, host, tmp_path, caplog,
    ):
        """When the swap can't cleanly close the old connection,
        kind=close_failed must fire at WARNING (spec §3)."""
        import logging as _logging
        caplog.set_level(_logging.WARNING, logger="src.chat_db_tx")

        class _BadCloseConn:
            in_transaction = True
            def rollback(self):
                raise sqlite3.OperationalError("forced rollback fail")
            def close(self):
                raise sqlite3.OperationalError("forced close fail")

        host._conn = _BadCloseConn()
        host.path = str(tmp_path / "swap.db")
        host._check_or_recover_at_depth_zero("test_method")
        # New conn was opened despite the close failure.
        assert host._conn is not None
        assert host._conn != _BadCloseConn()  # different class entirely
        close_warnings = [r for r in caplog.records
                          if "kind=close_failed" in r.getMessage()
                          and "test_method" in r.getMessage()]
        assert close_warnings, "expected one close_failed warning"
        host._conn.close()


class TestChatDbIntegration:
    def test_trace_callback_fires_on_real_chat_db_traffic(
        self, monkeypatch, tmp_path, caplog
    ):
        from src.chat_db import ChatDB
        monkeypatch.setenv("CHAT_DB_TRACE", "1")
        caplog.set_level(logging.DEBUG, logger="src.chat_db_tx")
        db = ChatDB(str(tmp_path / "integration.db"))
        db.register_agent("agent-a", "/tmp/a")
        db.insert_message("agent-a", "agent-b", "hi", "notify")
        kinds = [r.getMessage() for r in caplog.records
                 if "chatdb.trace" in r.getMessage()]
        assert kinds, "no trace lines captured during real ChatDB ops"
        joined = " ".join(kinds).upper()
        assert "BEGIN" in joined or "COMMIT" in joined, (
            "trace expected at least one transaction boundary"
        )

    def test_no_trace_lines_when_env_unset(
        self, monkeypatch, tmp_path, caplog
    ):
        from src.chat_db import ChatDB
        monkeypatch.delenv("CHAT_DB_TRACE", raising=False)
        caplog.set_level(logging.DEBUG, logger="src.chat_db_tx")
        db = ChatDB(str(tmp_path / "quiet.db"))
        db.register_agent("agent-a", "/tmp/a")
        db.insert_message("agent-a", "agent-b", "hi", "notify")
        kinds = [r.getMessage() for r in caplog.records
                 if "chatdb.trace" in r.getMessage()]
        assert kinds == [], (
            "trace callback emitted lines without CHAT_DB_TRACE=1"
        )

    def test_db_lock_attribute_present_on_chat_db(self, tmp_path):
        from src.chat_db import ChatDB
        db = ChatDB(str(tmp_path / "lock.db"))
        # ChatDB exposes _db_lock as a reentrant lock — re-acquire from
        # the same thread (a plain Lock would deadlock).
        with db._db_lock:
            with db._db_lock:
                pass
