"""Shared pytest helpers for the Phase 1 tx-wrapper tests."""
import sqlite3
import threading
from contextlib import contextmanager


@contextmanager
def sidecar_writer_lock(path: str):
    """Hold a write transaction on `path` via a sidecar sqlite3 connection.

    Yields a threading.Event the caller can set() to signal the sidecar
    to commit and release. Used to deterministically provoke
    `OperationalError("database is locked")` against another connection
    whose busy_timeout is shorter than the test's wait.
    """
    release = threading.Event()
    started = threading.Event()

    def _hold():
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("CREATE TABLE IF NOT EXISTS _sidecar_lock (id INTEGER)")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO _sidecar_lock (id) VALUES (1)")
        started.set()
        release.wait(timeout=10)
        conn.commit()
        conn.close()

    t = threading.Thread(target=_hold, daemon=True)
    t.start()
    started.wait(timeout=5)
    try:
        yield release
    finally:
        release.set()
        t.join(timeout=5)


def narrow_busy_timeout(db, ms: int = 50) -> None:
    """Lower the wrapped ChatDB's busy_timeout so a held writer lock
    surfaces as OperationalError before the sidecar releases."""
    db._conn.execute(f"PRAGMA busy_timeout={ms}")
