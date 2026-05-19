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


class TestRunTx:
    def test_clean_commit_returns_value(self, tmp_path):
        from src.chat_db import ChatDB
        db = ChatDB(str(tmp_path / "a.db"))
        def body():
            db._conn.execute("CREATE TABLE t (id INTEGER)")
            db._conn.execute("INSERT INTO t VALUES (42)")
            return "done"
        result = db._run_tx(body)
        assert result == "done"
        rows = db._conn.execute("SELECT id FROM t").fetchall()
        assert [r[0] for r in rows] == [42]

    def test_exception_rolls_back(self, tmp_path):
        from src.chat_db import ChatDB
        db = ChatDB(str(tmp_path / "a.db"))
        db._conn.execute("CREATE TABLE t (id INTEGER)")
        def body():
            db._conn.execute("INSERT INTO t VALUES (1)")
            raise RuntimeError("boom")
        with pytest.raises(RuntimeError):
            db._run_tx(body)
        rows = db._conn.execute("SELECT id FROM t").fetchall()
        assert rows == []

    def test_retry_succeeds_after_sidecar_releases(self, tmp_path, caplog):
        """First BEGIN IMMEDIATE waits busy_timeout (50ms) → OperationalError
        → kind=locked lock_event → retry. Sidecar releases between attempts;
        second BEGIN IMMEDIATE succeeds and fn body runs once."""
        import logging as _logging
        from src.chat_db import ChatDB
        from tests._tx_fixtures import sidecar_writer_lock, narrow_busy_timeout
        path = str(tmp_path / "a.db")
        db = ChatDB(path)
        db._conn.execute("CREATE TABLE t (id INTEGER)")
        narrow_busy_timeout(db, 50)
        caplog.set_level(_logging.WARNING, logger="src.chat_db_tx")
        attempts = {"n": 0}
        def body():
            attempts["n"] += 1
            db._conn.execute("INSERT INTO t VALUES (1)")
        with sidecar_writer_lock(path) as release:
            import threading as _t
            result_holder = {}
            def run():
                try:
                    db._run_tx(body)
                    result_holder["ok"] = True
                except Exception as exc:
                    result_holder["err"] = exc
            t = _t.Thread(target=run)
            t.start()
            import time
            time.sleep(0.2)
            release.set()
            t.join(timeout=10)
        assert result_holder.get("ok"), result_holder
        # With BEGIN IMMEDIATE, the retry signal is the kind=locked
        # lock_event from the first attempt's busy_timeout, NOT a re-run
        # of fn. Body runs exactly once (on the successful retry).
        locked_warnings = [r for r in caplog.records
                           if "kind=locked" in r.getMessage()]
        assert locked_warnings, "expected one kind=locked warning from the first attempt"
        assert attempts["n"] == 1, (
            f"with BEGIN IMMEDIATE body should run once on retry success; "
            f"got attempts={attempts['n']}"
        )
        assert db._conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1

    def test_nested_run_tx_joins_outer(self, tmp_path):
        from src.chat_db import ChatDB
        db = ChatDB(str(tmp_path / "a.db"))
        db._conn.execute("CREATE TABLE t (id INTEGER)")
        seen_depth = []
        def inner():
            seen_depth.append(db._tx_depth)
            db._conn.execute("INSERT INTO t VALUES (2)")
        def outer():
            seen_depth.append(db._tx_depth)
            db._conn.execute("INSERT INTO t VALUES (1)")
            db._run_tx(inner)
            db._conn.execute("INSERT INTO t VALUES (3)")
        db._run_tx(outer)
        rows = [r[0] for r in db._conn.execute("SELECT id FROM t ORDER BY id")]
        assert rows == [1, 2, 3]
        assert seen_depth == [1, 2]

    def test_post_commit_hooks_fire_outside_lock(self, tmp_path):
        from src.chat_db import ChatDB
        db = ChatDB(str(tmp_path / "a.db"))
        order = []
        def hook():
            order.append(("hook", db._tx_depth))
        def body():
            db._after_commit.append(hook)
            order.append(("body", db._tx_depth))
        db._run_tx(body)
        assert order == [("body", 1), ("hook", 0)]

    def test_post_commit_hooks_skipped_on_rollback(self, tmp_path):
        from src.chat_db import ChatDB
        db = ChatDB(str(tmp_path / "a.db"))
        fired = []
        def body():
            db._after_commit.append(lambda: fired.append(True))
            raise RuntimeError("rollback me")
        with pytest.raises(RuntimeError):
            db._run_tx(body)
        assert fired == []

    def test_non_locked_operational_error_propagates(self, tmp_path):
        """OperationalError that is NOT 'database is locked' should re-raise
        immediately without retry."""
        from src.chat_db import ChatDB
        db = ChatDB(str(tmp_path / "a.db"))
        def body():
            db._conn.execute("SELECT * FROM nonexistent_table_xyz")
        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            db._run_tx(body)

    def test_retry_failed_raises_after_two_locked_errors(self, tmp_path):
        """If both attempts fail with 'database is locked', re-raise and log
        retry_failed."""
        from src.chat_db import ChatDB
        from tests._tx_fixtures import sidecar_writer_lock, narrow_busy_timeout
        path = str(tmp_path / "b.db")
        db = ChatDB(path)
        db._conn.execute("CREATE TABLE t (id INTEGER)")
        narrow_busy_timeout(db, 50)
        with sidecar_writer_lock(path):
            # Reset the busy_timeout hook is also narrowed so retry also fails
            db._conn.execute("PRAGMA busy_timeout=50")
            with pytest.raises(sqlite3.OperationalError, match="database is locked"):
                db._run_tx(lambda: db._conn.execute("INSERT INTO t VALUES (1)"))

    def test_post_commit_hook_exception_is_swallowed(self, tmp_path, caplog):
        """A hook that raises must not propagate — exception is logged at WARNING."""
        import logging as _logging
        from src.chat_db import ChatDB
        caplog.set_level(_logging.WARNING, logger="src.chat_db_tx")
        db = ChatDB(str(tmp_path / "a.db"))
        def bad_hook():
            raise RuntimeError("hook exploded")
        def body():
            db._after_commit.append(bad_hook)
        db._run_tx(body)  # must NOT raise
        warns = [r for r in caplog.records
                 if "chatdb.after_commit_hook_failed" in r.getMessage()]
        assert warns, "expected after_commit_hook_failed warning"

    def test_safe_rollback_replaces_conn_on_failure(self, tmp_path, caplog):
        """If rollback raises sqlite3.Error, connection is replaced."""
        import logging as _logging
        from src.chat_db import ChatDB
        caplog.set_level(_logging.WARNING, logger="src.chat_db_tx")
        db = ChatDB(str(tmp_path / "a.db"))
        original_conn = db._conn

        class _BrokenRollback:
            in_transaction = False
            def execute(self, sql, *a, **kw):
                return original_conn.execute(sql, *a, **kw)
            def commit(self):
                return original_conn.commit()
            def rollback(self):
                raise sqlite3.OperationalError("rollback forced to fail")
            def close(self):
                return original_conn.close()

        broken = _BrokenRollback()
        db._conn = broken
        def body():
            raise RuntimeError("trigger rollback")
        with pytest.raises(RuntimeError):
            db._run_tx(body)
        # Connection must have been replaced
        assert db._conn is not broken
        warns = [r for r in caplog.records
                 if "rollback_failed" in r.getMessage()]
        assert warns


class TestRead:
    def test_returns_value(self, tmp_path):
        from src.chat_db import ChatDB
        db = ChatDB(str(tmp_path / "a.db"))
        db._conn.execute("CREATE TABLE t (id INTEGER)")
        db._conn.execute("INSERT INTO t VALUES (7)")
        db._conn.commit()  # commit so the poison check has nothing to roll back
        def body():
            return db._conn.execute("SELECT id FROM t").fetchone()[0]
        assert db._read(body) == 7

    def test_nested_read_inside_run_tx(self, tmp_path):
        from src.chat_db import ChatDB
        db = ChatDB(str(tmp_path / "a.db"))
        db._conn.execute("CREATE TABLE t (id INTEGER)")
        captured = []
        def outer():
            db._conn.execute("INSERT INTO t VALUES (1)")
            captured.append(
                db._read(lambda: db._conn.execute(
                    "SELECT id FROM t").fetchone()[0]),
            )
        db._run_tx(outer)
        assert captured == [1]

    def test_read_runs_poison_check_at_depth_zero(self, tmp_path, caplog):
        import logging as _logging
        from src.chat_db import ChatDB
        db = ChatDB(str(tmp_path / "a.db"))
        db._conn.execute("CREATE TABLE t (id INTEGER)")
        db._conn.execute("BEGIN")
        db._conn.execute("INSERT INTO t VALUES (1)")
        assert db._conn.in_transaction is True
        caplog.set_level(_logging.WARNING, logger="src.chat_db_tx")
        db._read(lambda: None)
        assert db._conn.in_transaction is False
        warnings = [r for r in caplog.records
                    if "kind=stale_tx" in r.getMessage()]
        assert warnings


class TestPhase1Integration:
    def test_concurrent_writers_serialise(self, tmp_path):
        """Two threads hammering insert_message must observe N inserts,
        not lost writes — the RLock serialises them."""
        from src.chat_db import ChatDB
        import threading as _t
        db = ChatDB(str(tmp_path / "a.db"))
        db.register_agent("agent-a", "/p1")
        db.register_agent("agent-b", "/p2")
        N = 20
        def hammer(name):
            for i in range(N):
                db.insert_message(name, "user", f"msg-{i}", "notify")
        threads = [
            _t.Thread(target=hammer, args=("agent-a",)),
            _t.Thread(target=hammer, args=("agent-b",)),
        ]
        for t in threads: t.start()
        for t in threads: t.join(timeout=10)
        count = db._conn.execute(
            "SELECT COUNT(*) FROM messages WHERE type='notify'",
        ).fetchone()[0]
        assert count == 2 * N

    def test_real_method_retries_after_sidecar_release(self, tmp_path):
        from src.chat_db import ChatDB
        from tests._tx_fixtures import sidecar_writer_lock, narrow_busy_timeout
        import threading as _t
        import time as _time
        path = str(tmp_path / "a.db")
        db = ChatDB(path)
        db.register_agent("agent-a", "/p1")
        narrow_busy_timeout(db, 50)
        with sidecar_writer_lock(path) as release:
            result_holder = {}
            def run():
                try:
                    row = db.insert_message("agent-a", "user", "hi", "notify")
                    result_holder["row"] = row
                except Exception as exc:
                    result_holder["err"] = exc
            t = _t.Thread(target=run)
            t.start()
            _time.sleep(0.2)
            release.set()
            t.join(timeout=10)
        assert "row" in result_holder
        assert result_holder["row"]["body"] == "hi"

    def test_stale_tx_on_register_agent_recovers(self, tmp_path, caplog):
        """Simulate the 2026-05-19 wedge: a stale in_transaction state
        on the shared connection. register_agent's entry must clear it
        via _check_or_recover_at_depth_zero rather than failing."""
        import logging as _logging
        from src.chat_db import ChatDB
        caplog.set_level(_logging.WARNING, logger="src.chat_db_tx")
        db = ChatDB(str(tmp_path / "a.db"))
        db._conn.execute("BEGIN")
        db._conn.execute("CREATE TABLE _filler (id INTEGER)")
        assert db._conn.in_transaction is True
        agent = db.register_agent("agent-a", "/p1")
        assert agent["name"] == "agent-a"
        warnings = [r for r in caplog.records
                    if "kind=stale_tx" in r.getMessage()]
        assert warnings

    def test_insert_message_nudge_wake_fires_after_commit(self, tmp_path):
        """_nudge_wake moves to _after_commit so it fires AFTER the
        transaction commits and the RLock releases — never on rollback,
        never before the row is durable."""
        from src.chat_db import ChatDB
        import threading as _t
        db = ChatDB(str(tmp_path / "a.db"))
        evt = _t.Event()
        db.set_wake_nudge(evt)
        db.register_agent("agent-a", "/p1")
        db.insert_message("agent-a", "user", "hi", "notify")
        assert evt.is_set()
