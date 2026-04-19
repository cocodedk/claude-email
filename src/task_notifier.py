"""Guaranteed task-completion notification.

Deterministic counterpart to chat_notify: the worker calls notify_task_done
after every terminal state, so the user always gets an email even if the
spawned claude forgot to call chat_notify itself.

Insert goes straight into the messages table; claude-email's outbound
relay picks it up in the next poll and threads it into a reply.
"""
import logging
from pathlib import Path

from src.chat_db import ChatDB

logger = logging.getLogger(__name__)


def notify_task_done(db_path: str, task_row: dict) -> None:
    """Queue an agent→user notification describing task completion.

    Non-raising: DB errors are logged but never propagate into the worker.
    """
    if not task_row:
        return
    try:
        db = ChatDB(db_path)
        db.insert_message(
            _from_name(task_row),
            "user",
            _body(task_row),
            "notify",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("notify_task_done failed: %s", exc)


def _from_name(task_row: dict) -> str:
    path = task_row.get("project_path") or ""
    return "agent-" + (Path(path).name or "unknown")


_EXCERPT_LIMIT = 600


def _excerpt(text: str | None) -> str:
    if not text:
        return ""
    t = text.strip()
    if len(t) <= _EXCERPT_LIMIT:
        return t
    return t[-_EXCERPT_LIMIT:]


def _body(task_row: dict) -> str:
    tid = task_row.get("id")
    status = task_row.get("status")
    branch = task_row.get("branch_name")
    project = Path(task_row.get("project_path") or "").name
    out_tail = _excerpt(task_row.get("output_text"))
    header = f"[{project}] Task #{tid} {status}"
    if status == "failed":
        err = (task_row.get("error_text") or "").strip()
        parts = [f"{header}: {err[:400]}" if err else header]
        if out_tail:
            parts.append("--- tail of output ---")
            parts.append(out_tail)
        return "\n".join(parts)
    if status == "cancelled":
        return header + "."
    # done
    branch_line = (
        f" on branch `{branch}`. Run `git log -1 {branch}` to see the changes."
        if branch else " (non-git project, no branch recorded)."
    )
    if out_tail:
        return f"{header}{branch_line}\n\n--- tail of output ---\n{out_tail}"
    return f"{header}{branch_line}"
