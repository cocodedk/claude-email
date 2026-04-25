"""Emit kind=status envelopes for in-flight tasks.

Covers the two locked mid-flight states — ``stalled`` and
``waiting-on-peer`` — agreed with agent-Claude-Email-App. Terminal
states stay on kind=result. Emission is deduplicated via
``tasks.last_sent_status`` so the same status doesn't re-fire every
tick.
"""
import logging
from pathlib import Path
from typing import Any

from src.chat_db import ChatDB
from src.json_envelope import CONTENT_TYPE as _JSON_CT, build_envelope

logger = logging.getLogger(__name__)


STATUS_CODES = {"stalled", "waiting-on-peer"}


def emit_status(
    db: ChatDB, task_id: int, status: str, *,
    reason: str = "",
    retry_after_seconds: int | None = None,
    last_activity_at: str = "",
) -> bool:
    """Insert a status notify message for ``task_id`` iff the status
    differs from the last one sent. Returns True if a message was
    emitted, False if deduped or the task is unknown.

    Mirrors notify_task_done's content-type handling: JSON envelope for
    JSON-origin tasks, plain-text body otherwise. ``retry_after_seconds``
    is only carried on ``stalled`` (silently dropped on other states to
    keep the envelope clean).
    """
    if status not in STATUS_CODES:
        raise ValueError(
            f"unknown status code {status!r}; add to STATUS_CODES",
        )
    row = db._conn.execute(  # noqa: SLF001 — same-package coupling
        "SELECT project_path, last_sent_status, origin_content_type "
        "FROM tasks WHERE id=?",
        (task_id,),
    ).fetchone()
    if row is None:
        return False
    if row["last_sent_status"] == status:
        return False
    data: dict[str, Any] = {"status": status}
    if last_activity_at:
        data["last_activity_at"] = last_activity_at
    if reason:
        data["reason"] = reason
    if status == "stalled" and retry_after_seconds is not None:
        data["retry_after_seconds"] = int(retry_after_seconds)
    from_name = "agent-" + (Path(row["project_path"]).name or "unknown")
    is_json = (row["origin_content_type"] or "") == _JSON_CT
    if is_json:
        body = build_envelope(
            "status", body=f"Task #{task_id} {status}",
            task_id=task_id, data=data,
        )
        content_type = _JSON_CT
    else:
        body = _plain_body(task_id, status, data)
        content_type = ""  # text/plain — relay default
    # Persist dedup mark BEFORE the envelope inserts. If insert_message
    # raises (locked DB, disk full) after the mark is committed, the
    # next tick dedups into a silent skip — strictly better than a
    # double-emit the client would render as a glyph flicker.
    db._conn.execute(  # noqa: SLF001
        "UPDATE tasks SET last_sent_status=? WHERE id=?", (status, task_id),
    )
    db._conn.commit()
    db.insert_message(
        from_name, "user", body, "notify",
        content_type=content_type, task_id=task_id,
    )
    return True


def _plain_body(task_id: int, status: str, data: dict) -> str:
    parts = [f"Task #{task_id} {status}"]
    if data.get("reason"):
        parts.append(f"Reason: {data['reason']}")
    if data.get("retry_after_seconds") is not None:
        parts.append(f"Retry after: {data['retry_after_seconds']}s")
    if data.get("last_activity_at"):
        parts.append(f"Last activity: {data['last_activity_at']}")
    return "\n".join(parts)


def clear_status_dedup(db: ChatDB, task_id: int) -> None:
    """Clear ``last_sent_status`` for ``task_id`` so the next entry into any
    mid-flight state emits a fresh envelope. Call sites: ask_user after a
    reply lands, wake_watcher after a turn delivers progress. Without this,
    a task that re-enters the same state later (a second chat_ask, a
    recovered-then-stalled wake) would be silently deduped."""
    db._conn.execute(  # noqa: SLF001
        "UPDATE tasks SET last_sent_status=NULL WHERE id=?", (task_id,),
    )
    db._conn.commit()


def clear_status_dedup_for_project(db: ChatDB, project_path: str) -> None:
    """Clear ``last_sent_status`` for the running task in ``project_path``.
    Wake-watcher entry point — used when a turn delivers messages so a
    later stall emits a fresh envelope instead of being deduped."""
    db._conn.execute(  # noqa: SLF001
        "UPDATE tasks SET last_sent_status=NULL "
        "WHERE project_path=? AND status='running'",
        (project_path,),
    )
    db._conn.commit()


def emit_stalled_for_project(
    db: ChatDB, project_path: str, reason: str = "",
) -> bool:
    """Find the running task (if any) in ``project_path`` and emit a
    ``stalled`` status envelope. No-op when no task is running — this is
    the wake_watcher's entry point and agent-level stalls without a
    running task have nothing task-linked to surface. Swallows its own
    exceptions so call sites stay one-liners that can't break wake."""
    try:
        row = db._conn.execute(  # noqa: SLF001
            "SELECT id FROM tasks WHERE project_path=? AND status='running' LIMIT 1",
            (project_path,),
        ).fetchone()
        if row is None:
            return False
        return emit_status(db, row["id"], "stalled", reason=reason)
    except Exception:
        logger.exception("emit_stalled_for_project failed for %s", project_path)
        return False
