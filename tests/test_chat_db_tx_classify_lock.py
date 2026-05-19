"""Tests for the ChatDB transaction wrapper layer."""
import pytest

from src.chat_db_tx import TransactionMixin


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
