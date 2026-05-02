"""Per-project FIFO task queue over the chat-db SQLite file.

Tasks are ordered by (priority DESC, id ASC) so higher-priority work
jumps the queue while same-priority stays strictly FIFO.
Atomic claim is a single UPDATE...WHERE id=(SELECT...) guarded by the
status='pending' check so two concurrent callers can't both claim the
same row.

TaskQueue holds its own sqlite connection — the schema lives in
ChatDB._SCHEMA (IF NOT EXISTS), so instantiate ChatDB at least once on
the target DB file before constructing TaskQueue.
"""
import sqlite3
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskQueue:
    def __init__(self, path: str):
        self.path = path
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")

    def enqueue(
        self, project_path: str, body: str, priority: int = 0,
        retry_of: int | None = None, plan_first: bool = False,
        origin_content_type: str = "", origin_message_id: str = "",
        origin_subject: str = "", origin_from: str = "",
    ) -> int:
        cur = self._conn.execute(
            "INSERT INTO tasks (project_path, body, priority, created_at, retry_of, "
            "plan_first, origin_content_type, origin_message_id, origin_subject, "
            "origin_from) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (project_path, body, priority, _now(), retry_of,
             1 if plan_first else 0, origin_content_type or None,
             origin_message_id or None, origin_subject or None,
             origin_from or None),
        )
        self._conn.commit()
        return cur.lastrowid

    def claim_next(self, project_path: str) -> dict | None:
        """Atomically move the oldest pending task for a project to running."""
        cur = self._conn.execute(
            "UPDATE tasks SET status='running', started_at=? "
            "WHERE id=(SELECT id FROM tasks "
            "          WHERE project_path=? AND status='pending' "
            "          ORDER BY priority DESC, id ASC LIMIT 1) "
            "AND status='pending' RETURNING *",
            (_now(), project_path),
        )
        row = cur.fetchone()
        self._conn.commit()
        return dict(row) if row else None

    def mark_done(self, task_id: int) -> None:
        self._conn.execute(
            "UPDATE tasks SET status='done', completed_at=? WHERE id=?",
            (_now(), task_id),
        )
        self._conn.commit()

    def mark_failed(self, task_id: int, error_text: str) -> None:
        self._conn.execute(
            "UPDATE tasks SET status='failed', error_text=?, completed_at=? WHERE id=?",
            (error_text, _now(), task_id),
        )
        self._conn.commit()

    def cancel(self, task_id: int) -> None:
        self._conn.execute(
            "UPDATE tasks SET status='cancelled', completed_at=? WHERE id=?",
            (_now(), task_id),
        )
        self._conn.commit()

    def set_pid(self, task_id: int, pid: int) -> None:
        self._conn.execute("UPDATE tasks SET pid=? WHERE id=?", (pid, task_id))
        self._conn.commit()

    def set_branch(self, task_id: int, branch_name: str) -> None:
        self._conn.execute(
            "UPDATE tasks SET branch_name=? WHERE id=?", (branch_name, task_id),
        )
        self._conn.commit()

    def set_output(self, task_id: int, output_text: str) -> None:
        self._conn.execute(
            "UPDATE tasks SET output_text=? WHERE id=?", (output_text, task_id),
        )
        self._conn.commit()

    def list_pending(self, project_path: str) -> list[dict]:
        cur = self._conn.execute(
            "SELECT * FROM tasks WHERE project_path=? AND status='pending' "
            "ORDER BY priority DESC, id ASC",
            (project_path,),
        )
        return [dict(r) for r in cur.fetchall()]

    def get_running(self, project_path: str) -> dict | None:
        cur = self._conn.execute(
            "SELECT * FROM tasks WHERE project_path=? AND status='running' LIMIT 1",
            (project_path,),
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def list_running(self) -> list[dict]:
        """Every running task across all projects. Used by the ghost reaper."""
        cur = self._conn.execute(
            "SELECT * FROM tasks WHERE status='running' ORDER BY id",
        )
        return [dict(r) for r in cur.fetchall()]

    def drain_pending(self, project_path: str) -> int:
        """Cancel all pending (not running) tasks for a project. Returns count."""
        cur = self._conn.execute(
            "UPDATE tasks SET status='cancelled', completed_at=? "
            "WHERE project_path=? AND status='pending'",
            (_now(), project_path),
        )
        self._conn.commit()
        return cur.rowcount

    def get(self, task_id: int) -> dict | None:
        cur = self._conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def list_project_paths(self) -> list[str]:
        """Return every project_path that has ever had a task."""
        cur = self._conn.execute(
            "SELECT DISTINCT project_path FROM tasks ORDER BY project_path",
        )
        return [r["project_path"] for r in cur.fetchall()]

    def latest_task(self, project_path: str) -> dict | None:
        cur = self._conn.execute(
            "SELECT * FROM tasks WHERE project_path=? ORDER BY id DESC LIMIT 1",
            (project_path,),
        )
        row = cur.fetchone()
        return dict(row) if row else None
