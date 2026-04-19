"""Per-project task audit log — writes two files in <project>/.claude/.

Called when a task terminates (done/failed/cancelled) with the row from
the tasks table. Appends:

- tasks.jsonl: one JSON object per line — machine-readable history.
- CHANGELOG-claude.md: one markdown block per task — human-readable.

Filesystem failures are logged as warnings but never raise; an audit log
should not take down the worker. The body column has already passed
through extract_command(strip_secret=...) before landing in the queue,
so no AUTH: tokens leak into these files.
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_CLAUDE_DIR = ".claude"
_JSONL = "tasks.jsonl"
_MD = "CHANGELOG-claude.md"


def log_task_finished(project_path: str, task_row: dict) -> None:
    """Append a finished-task record to both log files.

    No-op if project_path is missing or mkdir fails; never raises.
    """
    try:
        claude_dir = Path(project_path) / _CLAUDE_DIR
        claude_dir.mkdir(exist_ok=True)
    except OSError as exc:
        logger.warning("task_log: cannot prepare %s/.claude: %s", project_path, exc)
        return
    entry = {
        "id": task_row["id"],
        "status": task_row["status"],
        "body": task_row["body"],
        "branch": task_row.get("branch_name"),
        "received_at": task_row.get("created_at"),
        "started_at": task_row.get("started_at"),
        "completed_at": task_row.get("completed_at"),
        "error": task_row.get("error_text"),
    }
    _append_jsonl(claude_dir / _JSONL, entry)
    _append_markdown(claude_dir / _MD, entry)


def _append_jsonl(path: Path, obj: dict) -> None:
    try:
        with path.open("a") as f:
            f.write(json.dumps(obj) + "\n")
    except OSError as exc:
        logger.warning("task_log jsonl failed: %s", exc)


def _append_markdown(path: Path, entry: dict) -> None:
    outcome = {"done": "✓", "failed": "✗", "cancelled": "—"}.get(entry["status"], "?")
    lines = [
        "",
        f"## Task #{entry['id']} — {entry.get('received_at', '?')} "
        f"→ {entry.get('completed_at', '?')} {outcome}",
        f"**Request:** {(entry.get('body') or '').strip()[:500]}",
    ]
    if entry.get("branch"):
        lines.append(f"**Branch:** `{entry['branch']}`")
    lines.append(f"**Status:** {entry['status']}")
    if entry.get("error"):
        lines.append(f"**Error:** {entry['error']}")
    lines.append("")
    try:
        with path.open("a") as f:
            f.write("\n".join(lines))
    except OSError as exc:
        logger.warning("task_log md failed: %s", exc)
