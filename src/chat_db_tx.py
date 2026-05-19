"""Connection lifecycle helpers for ChatDB (and reusable for other SQLite
callers in this project, e.g. TaskQueue).

Owns the canonical sqlite3 connection factory: WAL mode, foreign keys,
short busy_timeout (spec §6), and an optional env-flagged SQL trace
callback. Future transaction-wrapper machinery (`_run_tx`, `_read`,
stale-tx recovery, post-commit hooks) extends `TransactionMixin` here.

Spec: docs/superpowers/specs/2026-05-19-chatdb-tx-wrapper-design.md
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
from typing import Callable


_BUSY_TIMEOUT_MS = 200  # spec §6 — bound event-loop block per write
_TRACE_ENV_VAR = "CHAT_DB_TRACE"

logger = logging.getLogger(__name__)


def open_conn(
    path: str, trace_cb: Callable[[str], None] | None = None,
) -> sqlite3.Connection:
    """Open a sqlite3 connection with the project's canonical pragmas.

    ``trace_cb`` is installed iff ``CHAT_DB_TRACE`` is set; pass the bound
    method that owns transaction-state introspection. Reusable by any
    SQLite caller in this project that wants the same WAL + 200ms-timeout
    setup (e.g. TaskQueue).
    """
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys=ON")
    if trace_cb is not None and os.environ.get(_TRACE_ENV_VAR):
        conn.set_trace_callback(trace_cb)
    return conn


class TransactionMixin:
    """Adds connection lifecycle helpers to ChatDB."""

    # Class-level defaults so mixin-isolation tests and partially-constructed
    # hosts don't AttributeError. ChatDB sets these in its own __init__.
    _conn: sqlite3.Connection | None = None
    _db_lock: threading.RLock | None = None

    def _open_conn(self, path: str) -> sqlite3.Connection:
        """Delegate to the module-level factory, binding the trace callback."""
        return open_conn(path, self._trace_cb)

    def _trace_cb(self, sql: str) -> None:
        kind = self._classify_sql(sql)
        in_tx = self._conn is not None and self._conn.in_transaction
        # thread_id and tx_depth join this line once the tx wrapper owns
        # the call context — they're not available from a bare sqlite3
        # trace callback.
        logger.debug(
            "chatdb.trace kind=%s in_transaction=%s",
            kind, in_tx,
        )

    def _init_db_lock(self) -> None:
        """Attach an RLock if one hasn't been set yet (idempotent)."""
        if self._db_lock is None:
            self._db_lock = threading.RLock()

    @staticmethod
    def _classify_sql(sql: str) -> str:
        # SQLite's trace_v2 can pass None on some build configurations.
        head = (sql or "").strip().split(None, 1)
        if not head:
            return "OTHER"
        first = head[0].upper()
        if first in ("BEGIN", "COMMIT", "ROLLBACK"):
            return first
        return "OTHER"
