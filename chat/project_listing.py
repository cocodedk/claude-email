"""List-projects tool — split from chat/project_tools.py to stay under
the 200-line cap. Returns a per-project row carrying running/queue
state plus envelope-version-gated agent_status + task_state."""
import os
from pathlib import Path

from chat.project_helpers import last_activity
from src.task_queue import TaskQueue
from src.task_state import task_state_for_project


def list_projects_tool(
    queue: TaskQueue, *, allowed_base: str, chat_db=None,
    envelope_version: int = 1,
) -> dict:
    """Discover git repos under ``allowed_base`` + merge with task state.

    Project = a top-level directory containing a ``.git/`` entry. Hidden
    directories and plain files are skipped. Sorted by name so the row
    order is stable across polls.

    ``envelope_version`` controls the shape of each row:

    - ``<= 1`` (default): legacy ``agent_status`` vocabulary
      (``connected | disconnected | absent``); no ``task_state`` field.
    - ``>= 2``: new ``agent_status`` vocabulary
      (``online | stale | offline``) plus a ``task_state`` field
      (``waiting | working | completed | error | null``).

    Callers that don't pass ``chat_db`` get ``agent_status`` as
    ``"absent"`` (v: 1) or ``"offline"`` (v: 2). The field is always
    present for shape stability.
    """
    if not allowed_base:
        return {"projects": []}
    base = Path(allowed_base).resolve()
    try:
        entries = sorted(os.listdir(base))
    except OSError:
        return {"projects": []}
    use_v2 = envelope_version >= 2
    rows = []
    for entry in entries:
        if entry.startswith("."):
            continue
        path = base / entry
        if not path.is_dir() or not (path / ".git").exists():
            continue
        resolved = str(path)
        running = queue.get_running(resolved)
        row = {
            "name": entry,
            "path": resolved,
            "running_task_id": running["id"] if running else None,
            "queue_depth": len(queue.list_pending(resolved)),
            "last_activity_at": last_activity(queue.latest_task(resolved)),
            "agent_status": _agent_status(chat_db, resolved, use_v2),
        }
        if use_v2:
            row["task_state"] = task_state_for_project(queue, resolved)
        rows.append(row)
    return {"projects": rows}


def _agent_status(chat_db, resolved: str, use_v2: bool) -> str:
    if chat_db is None:
        return "offline" if use_v2 else "absent"
    if use_v2:
        return chat_db.agent_state_for_project(resolved)
    return chat_db.agent_status_for_project(resolved)
