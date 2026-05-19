"""Tests for the ChatDB transaction wrapper layer."""
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
        assert not isinstance(host._conn, _BadCloseConn)  # different class entirely
        close_warnings = [r for r in caplog.records
                          if "kind=close_failed" in r.getMessage()
                          and "test_method" in r.getMessage()]
        assert close_warnings, "expected one close_failed warning"
        host._conn.close()
