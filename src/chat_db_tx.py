"""Transaction wrapper layer for ChatDB.

Extracted from chat_db.py so that module stays under the 200-line cap
and so connection lifecycle (open, reopen, trace) is owned by one place.
Phase 0 only ships the connection factory + env-gated SQL trace callback.
Phase 1 will add _run_tx / _read / stale-tx recovery / post-commit hooks.

Spec: docs/superpowers/specs/2026-05-19-chatdb-tx-wrapper-design.md
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading


_BUSY_TIMEOUT_MS = 200  # spec §6 — bound event-loop block per write
_TRACE_ENV_VAR = "CHAT_DB_TRACE"

logger = logging.getLogger(__name__)


class TransactionMixin:
    """Adds connection lifecycle helpers to ChatDB."""

    # Hosts that haven't called _open_conn yet (e.g. mixin-isolation tests)
    # see _conn as None. ChatDB sets this in its own __init__.
    _conn: sqlite3.Connection | None = None
    # Placeholder for Phase 1 — ChatDB.__init__ calls _init_db_lock() so
    # _run_tx / _read consumers always find a live RLock.
    _db_lock: threading.RLock | None = None

    def _open_conn(self, path: str) -> sqlite3.Connection:
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA foreign_keys=ON")
        if os.environ.get(_TRACE_ENV_VAR):
            conn.set_trace_callback(self._trace_cb)
        return conn

    def _trace_cb(self, sql: str) -> None:
        kind = self._classify_sql(sql)
        in_tx = self._conn is not None and self._conn.in_transaction
        # thread_id and tx_depth from spec §7 ship with Phase 1's _run_tx,
        # where they'll be available from the call context.
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
