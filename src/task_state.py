"""Task-state derivation for envelope v: 2 list_projects responses.

Maps the tasks table's storage vocabulary
(``pending | running | done | failed | cancelled``) to the wire
vocabulary the app and dashboard render:

  waiting | working | completed | error | None

Precedence inside a project:

  running task       → working
  pending tasks      → waiting
  latest terminal task within fade window
      status=done       → completed
      status=cancelled  → completed
      status=failed     → error
  no recent task     → None

Fade window is controlled by ``TASK_STATE_FADE_SEC`` (default 30 s).
"""
import os
from datetime import datetime, timedelta, timezone

DEFAULT_TASK_STATE_FADE_SEC = 30


def _fade_secs() -> int:
    raw = os.environ.get("TASK_STATE_FADE_SEC")
    if raw is None:
        return DEFAULT_TASK_STATE_FADE_SEC
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_TASK_STATE_FADE_SEC


def task_state_for_project(
    queue, project_path: str, *, fade_secs: int | None = None,
) -> str | None:
    """See module docstring. ``fade_secs=None`` reads
    ``TASK_STATE_FADE_SEC`` env var, defaulting to 30."""
    if queue.get_running(project_path):
        return "working"
    if queue.list_pending(project_path):
        return "waiting"
    latest = queue.latest_task(project_path)
    if not latest:
        return None
    if fade_secs is None:
        fade_secs = _fade_secs()
    completed_at = latest.get("completed_at")
    if not completed_at:
        return None
    cutoff = (
        datetime.now(timezone.utc) - timedelta(seconds=fade_secs)
    ).isoformat()
    if completed_at < cutoff:
        return None
    status = latest.get("status")
    if status == "failed":
        return "error"
    if status in ("done", "cancelled"):
        return "completed"
    return None
