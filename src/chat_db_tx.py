"""Transaction wrapper layer for ChatDB.

Extracted from chat_db.py so that module stays under the 200-line cap
and so connection lifecycle (open, reopen, trace) is owned by one place.
Phase 0 only ships the connection factory + env-gated SQL trace callback.
Phase 1 will add _run_tx / _read / stale-tx recovery / post-commit hooks.

Spec: docs/superpowers/specs/2026-05-19-chatdb-tx-wrapper-design.md
"""
import logging
import os
import sqlite3


_BUSY_TIMEOUT_MS = 200  # spec §6 — bound event-loop block per write
_TRACE_ENV_VAR = "CHAT_DB_TRACE"

logger = logging.getLogger(__name__)


class TransactionMixin:
    """Adds connection lifecycle helpers to ChatDB."""

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
        """SQLite trace hook — Phase 0 stub overridden in Task 3."""
        return None
