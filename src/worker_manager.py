"""Per-project worker lifecycle — at most one worker process per canonical path.

The manager is owned by the claude-chat MCP server (one process on the host)
so its in-memory worker map is authoritative for the whole bus. When the
router enqueues a task via chat_enqueue_task, it calls ensure_worker(path);
if no alive worker covers that canonical path, a fresh `python -m
src.project_worker <path>` is spawned.

Canonical-path keying means /home/u/proj and /home/u/link-to-proj converge
on the same worker — codex flagged this as important for alias/nested-repo
handling.
"""
import logging
import os
import subprocess
import sys
from pathlib import Path

from src.process_liveness import is_alive

logger = logging.getLogger(__name__)

_REPO_ROOT = str(Path(__file__).resolve().parent.parent)


class WorkerManager:
    def __init__(
        self, *, db_path: str, project_root: str,
        python_bin: str | None = None,
        module_env: dict[str, str] | None = None,
    ):
        self._db_path = db_path
        self._project_root = str(Path(project_root).resolve())
        self._python_bin = python_bin or sys.executable
        self._module_env = module_env or {}
        self._workers: dict[str, subprocess.Popen] = {}

    def ensure_worker(self, project_path: str) -> int:
        """Spawn a worker for project_path if none alive. Returns its pid.

        Raises ValueError if project_path doesn't resolve to an existing dir.
        """
        resolved = self._resolve(project_path)
        existing = self._workers.get(resolved)
        if existing and existing.poll() is None and is_alive(existing.pid):
            return existing.pid
        if existing is not None:
            self._workers.pop(resolved, None)
        return self._spawn(resolved)

    def _resolve(self, project_path: str) -> str:
        path = Path(project_path).resolve()
        if not path.is_dir():
            raise ValueError(f"Project path does not exist: {project_path}")
        return str(path)

    def _spawn(self, resolved: str) -> int:
        argv = [self._python_bin, "-m", "src.project_worker", resolved]
        env = {**os.environ, **self._module_env}
        # cwd MUST be the claude-email repo so `python -m src.project_worker`
        # resolves. The worker itself sets cwd=resolved when it launches the
        # per-task `claude --continue --print`.
        logger.info("Spawning worker for %s", resolved)
        proc = subprocess.Popen(
            argv, cwd=_REPO_ROOT, shell=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=env,
        )
        self._workers[resolved] = proc
        return proc.pid

    def reap(self) -> None:
        """Drop any workers whose process is gone."""
        for resolved, proc in list(self._workers.items()):
            if proc.poll() is not None or not is_alive(proc.pid):
                self._workers.pop(resolved, None)

    def pid_of(self, project_path: str) -> int | None:
        try:
            resolved = self._resolve(project_path)
        except ValueError:
            return None
        proc = self._workers.get(resolved)
        if proc is None or proc.poll() is not None or not is_alive(proc.pid):
            return None
        return proc.pid

    def active_workers(self) -> dict[str, int]:
        return {path: proc.pid for path, proc in self._workers.items()}
