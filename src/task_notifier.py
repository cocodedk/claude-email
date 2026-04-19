"""Guaranteed task-completion notification.

Deterministic counterpart to chat_notify: the worker calls notify_task_done
after every terminal state, so the user always gets an email even if the
spawned claude forgot to call chat_notify itself.

When the task's origin_content_type is application/json, the notification
body is a JSON result envelope (kind=result, data.status/branch/output_tail)
and the message's content_type column is set so relay_outbound_messages
sends with matching Content-Type. Plain-text origins keep the human-
readable body.
"""
import logging
from pathlib import Path

from src.chat_db import ChatDB
from src.json_envelope import CONTENT_TYPE as _JSON_CT, build_envelope

logger = logging.getLogger(__name__)


def notify_task_done(db_path: str, task_row: dict) -> None:
    """Queue an agent→user notification describing task completion.

    Non-raising: DB errors are logged but never propagate into the worker.
    """
    if not task_row:
        return
    try:
        db = ChatDB(db_path)
        origin = task_row.get("origin_content_type") or ""
        if origin == _JSON_CT:
            body = _json_body(task_row)
            content_type = _JSON_CT
        else:
            body = _body(task_row)
            content_type = ""  # plain text — relay default
        db.insert_message(
            _from_name(task_row),
            "user",
            body,
            "notify",
            content_type=content_type,
            task_id=task_row.get("id"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("notify_task_done failed: %s", exc)


def _json_body(task_row: dict) -> str:
    status = task_row.get("status")
    data = {
        "status": status,
        "branch": task_row.get("branch_name"),
        "output_tail": task_row.get("output_text") or "",
    }
    if task_row.get("error_text"):
        data["error"] = task_row["error_text"]
    return build_envelope(
        "result",
        body=f"Task #{task_row.get('id')} {status}",
        task_id=task_row.get("id"),
        data=data,
    )


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
