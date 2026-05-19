"""Connection lifecycle helpers for ChatDB (and reusable for other SQLite
callers in this project, e.g. TaskQueue).

Owns the canonical sqlite3 connection factory: WAL mode, foreign keys,
short busy_timeout (spec §6), and an optional env-flagged SQL trace
callback. `TransactionMixin` composes these onto ChatDB.

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
    path: str = ""  # Set by host __init__; needed for _close_and_reopen.

    def _open_conn(self, path: str) -> sqlite3.Connection:
        """Delegate to the module-level factory, binding the trace callback."""
        return open_conn(path, self._trace_cb)

    def _trace_cb(self, sql: str) -> None:
        kind = self._classify_sql(sql)
        in_tx = self._conn is not None and self._conn.in_transaction
        # Bare sqlite3 trace callbacks don't expose thread or call-depth;
        # the surrounding caller has to add those fields when it logs.
        logger.debug(
            "chatdb.trace kind=%s in_transaction=%s",
            kind, in_tx,
        )

    def _init_db_lock(self) -> None:
        """Attach an RLock if one hasn't been set yet (idempotent)."""
        if self._db_lock is None:
            self._db_lock = threading.RLock()

    def _check_or_recover_at_depth_zero(self, method_name: str) -> None:
        """Entry guard for outermost _run_tx / _read frames.

        If self._conn is None or not in a transaction, no-op. Otherwise
        we found a leaked implicit tx — log the smoking-gun event, try
        rollback, and on rollback failure swap to a fresh connection.
        """
        if self._conn is None or not self._conn.in_transaction:
            return
        self._lock_event(method_name, "stale_tx", connection_replaced=False)
        try:
            self._conn.rollback()
        except sqlite3.Error:
            self._lock_event(method_name, "rollback_failed", connection_replaced=True)
            self._close_and_reopen()

    def _lock_event(
        self, method_name: str, kind: str, *, connection_replaced: bool = False,
    ) -> None:
        in_tx = self._conn is not None and self._conn.in_transaction
        logger.warning(
            "chatdb.lock_event method=%s kind=%s conn.in_transaction=%s "
            "connection_replaced=%s",
            method_name, kind, in_tx, connection_replaced,
        )

    def _close_and_reopen(self) -> None:
        """Best-effort close of self._conn, then open a fresh one."""
        old = self._conn
        try:
            old.close()
        except sqlite3.Error:
            pass  # GC will reclaim the fd
        self._conn = open_conn(self.path, self._trace_cb)

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
