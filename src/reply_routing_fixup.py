"""Safety-net stamp for ``tasks.origin_from`` after an email-driven dispatch.

The LLM-router subprocess routes plain-text emails to chat_enqueue_task
via MCP. The router prompt instructs it to pass ``origin_from`` so the
relay addresses [Update] / result replies to the actual inbound sender,
but if the LLM forgets, those tasks land with origin_from=NULL and the
relay falls back to the canonical AUTHORIZED_SENDER.

This module is the deterministic backstop. ``main.process_email`` calls
``run_router_with_fixup`` which wraps the LLM-router invocation with
the timestamp window + the post-execute stamp pass; any task created
in that window inside this universe with no origin_from is stamped
with the inbound sender. Workers take seconds to fire their first
[Update], so the stamp lands before relay_outbound_messages queries it.
"""
import logging
import os
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def stamp_origin_for_window(
    *, db_path: str, allowed_base: str, reply_to: str,
    started_at_iso: str, origin_message_id: str = "", origin_subject: str = "",
) -> int:
    """Stamp origin_from / origin_message_id / origin_subject on tasks
    created in the dispatch window.

    Only tasks whose project_path lives under ``allowed_base`` are
    touched — primary and test universes share the DB but must not stamp
    each other's tasks. ``origin_message_id`` is required by
    ``chat_relay._should_relay``: without it, async task-completion
    notifications get drained without an SMTP send.

    Returns the number of rows updated. Silent no-op when ``reply_to``
    or ``allowed_base`` is empty.
    """
    if not reply_to or not allowed_base:
        return 0
    base = allowed_base.rstrip("/")
    base_prefix = base + os.sep
    # Escape SQL-LIKE wildcards in the path prefix. ``_`` is a single-char
    # wildcard, so ``allowed_base=/tmp/foo_bar`` would otherwise match
    # ``/tmp/fooxbar/...`` and stamp tasks from a different universe.
    escaped_prefix = (
        base_prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "UPDATE tasks SET origin_from=?, "
            "  origin_message_id=COALESCE(origin_message_id, ?), "
            "  origin_subject=COALESCE(origin_subject, ?) "
            "WHERE origin_from IS NULL "
            "AND created_at >= ? "
            "AND (project_path = ? OR project_path LIKE ? ESCAPE '\\')",
            (
                reply_to, origin_message_id or None, origin_subject or None,
                started_at_iso, base, escaped_prefix + "%",
            ),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


# Back-compat alias — old signature without message-id / subject.
stamp_origin_from_for_window = stamp_origin_for_window


def run_router_with_fixup(
    execute_fn, *, db_path: str, allowed_base: str, reply_to: str,
    origin_message_id: str = "", origin_subject: str = "",
):
    """Time-window the LLM router run, then stamp any orphan tasks.

    ``execute_fn`` is the zero-arg closure that invokes ``execute_command``
    with whatever args main.py wants. Returns the executor's output
    unchanged. The stamp pass swallows its own exceptions so a buggy
    fixup never poisons the result reply.
    """
    started = datetime.now(timezone.utc).isoformat()
    output = execute_fn()
    if reply_to and db_path and allowed_base:
        try:
            stamped = stamp_origin_for_window(
                db_path=db_path, allowed_base=allowed_base,
                reply_to=reply_to, started_at_iso=started,
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
