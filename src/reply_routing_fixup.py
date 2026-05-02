"""Stamp ``tasks.origin_*`` after an email-driven dispatch.

``chat_enqueue_task``'s MCP schema deliberately refuses origin_*
arguments — trusting LLM-supplied values would let any bus client
hijack a task's reply address. This module is the trusted backstop:
``main.process_email`` calls ``run_router_with_fixup`` which captures
``max(tasks.id)`` before spawning the LLM router and a window-start
timestamp, then after the router returns stamps origin_from /
origin_message_id / origin_subject onto the freshly-created tasks
sourced from the trusted inbound headers.

Correlation: tasks created during the dispatch window have id >
``pre_max_id``. A window-only filter would also match concurrent
non-router enqueues from other MCP clients; if the post-window
candidate set has more than one origin-less row in this universe the
stamp aborts with a warning rather than risk routing an unrelated
task's [Update] to this dispatch's sender.
"""
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def _canonical_base(allowed_base: str) -> str:
    """Match enqueue_task_tool's ``Path(...).resolve()`` so symlinks /
    relative paths / ``..`` segments don't slip past the prefix
    comparison. Falls back to the input on resolution failure to keep
    the function total."""
    try:
        return str(Path(allowed_base).resolve())
    except Exception:  # pragma: no cover — defensive
        return allowed_base.rstrip("/")


def max_task_id(db_path: str) -> int:
    """Return the highest task id at the moment of the call (0 if empty
    or if the schema isn't yet initialized — the latter only happens in
    the test path where chat_db hasn't run init yet). Captured
    pre-dispatch so the post-dispatch fixup can scope itself to rows
    created during the LLM-router run."""
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.OperationalError:  # pragma: no cover — defensive (sqlite3.connect rarely raises)
        return 0
    try:
        row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM tasks").fetchone()
        return row[0] if row else 0
    except sqlite3.OperationalError:
        return 0
    finally:
        conn.close()


def stamp_origin_for_window(
    *, db_path: str, allowed_base: str, reply_to: str,
    started_at_iso: str, pre_max_id: int = 0,
    origin_message_id: str = "", origin_subject: str = "",
) -> int:
    """Stamp origin_from / origin_message_id / origin_subject on the task
    created during the dispatch window.

    Scoping (most specific first):
      1. ``id > pre_max_id`` — captured before spawning the router.
      2. ``created_at >= started_at_iso`` — defensive double-check.
      3. ``project_path`` lives under ``allowed_base`` (after resolution
         so symlinks / ``..`` don't slip past).
      4. ``origin_from IS NULL`` — never overwrite an existing stamp.

    If the candidate set has more than one row, abort with a warning —
    a concurrent non-router enqueue racing through the window is
    ambiguous, and stamping it would route the unrelated task's
    [Update] to this dispatch's sender. Returns the number of rows
    actually stamped.
    """
    if not reply_to or not allowed_base:
        return 0
    base = _canonical_base(allowed_base)
    base_prefix = base + os.sep
    # Escape SQL-LIKE wildcards in the path prefix. ``_`` is a single-char
    # wildcard, so ``allowed_base=/tmp/foo_bar`` would otherwise match
    # ``/tmp/fooxbar/...`` and stamp tasks from a different universe.
    escaped_prefix = (
        base_prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    conn = sqlite3.connect(db_path)
    try:
        candidates = conn.execute(
            "SELECT id FROM tasks "
            "WHERE origin_from IS NULL "
            "AND id > ? "
            "AND created_at >= ? "
            "AND (project_path = ? OR project_path LIKE ? ESCAPE '\\')",
            (pre_max_id, started_at_iso, base, escaped_prefix + "%"),
        ).fetchall()
        if not candidates:
            return 0
        if len(candidates) > 1:
            ids = [r[0] for r in candidates]
            logger.warning(
                "origin fixup: %d origin-less tasks created during dispatch "
                "window (%s) — refusing to stamp; relay will fall back to "
                "universe canonical for these", len(ids), ids,
            )
            return 0
        task_id = candidates[0][0]
        cur = conn.execute(
            "UPDATE tasks SET origin_from=?, "
            "  origin_message_id=COALESCE(origin_message_id, ?), "
            "  origin_subject=COALESCE(origin_subject, ?) "
            "WHERE id=? AND origin_from IS NULL",
            (reply_to, origin_message_id or None, origin_subject or None, task_id),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


# Back-compat alias — old signature without pre_max_id / message-id / subject.
stamp_origin_from_for_window = stamp_origin_for_window


def run_router_with_fixup(
    execute_fn, *, db_path: str, allowed_base: str, reply_to: str,
    origin_message_id: str = "", origin_subject: str = "",
):
    """Time-window the LLM router run, then stamp the new task.

    Captures ``max(tasks.id)`` and a window-start timestamp before
    invoking ``execute_fn``; after it returns, stamps origin_* on the
    one task created in that window inside this universe (with an
    ambiguous-multiple-tasks abort to avoid mis-routing concurrent
    non-router enqueues). Stamp failures are swallowed so a buggy
    fixup never poisons the result reply.
    """
    started = datetime.now(timezone.utc).isoformat()
    pre_max = max_task_id(db_path) if db_path else 0
    output = execute_fn()
    if reply_to and db_path and allowed_base:
        try:
            stamped = stamp_origin_for_window(
                db_path=db_path, allowed_base=allowed_base,
                reply_to=reply_to, started_at_iso=started,
                pre_max_id=pre_max,
                origin_message_id=origin_message_id,
                origin_subject=origin_subject,
            )
            if stamped:
                logger.info(
                    "stamped origin metadata (from=%s) on %d task(s) "
                    "the LLM router missed", reply_to, stamped,
                )
        except Exception:
            logger.exception("origin fixup failed")
    return output
