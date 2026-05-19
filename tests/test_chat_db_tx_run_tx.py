"""Tests for the ChatDB transaction wrapper layer."""
import sqlite3

import pytest


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
