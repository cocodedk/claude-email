"""Per-project worker loop — claims tasks from the queue and runs them.

Invoked by worker_manager as a subprocess: `python -m src.project_worker <path>`.
Reads claude/MCP config from environment variables so the CLI-invocation surface
stays tiny.

Shape of one task turn:
  claim_next(project) → Popen(claude --continue --print <body>) → wait →
    mark_done (exit 0) or mark_failed (nonzero/timeout)

Between turns the loop polls the queue; if no pending task appears for
`idle_timeout` seconds, the worker exits so we don't leave stale processes
around. worker_manager respawns on demand.
"""
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable

from src.git_ops import (
    checkout_new_branch, is_clean, is_git_repo, task_branch_name,
)
from src.task_log import log_task_finished
from src.task_notifier import notify_task_done
from src.task_queue import TaskQueue

logger = logging.getLogger(__name__)


@dataclass
class WorkerConfig:
    project_path: str
    db_path: str
    claude_bin: str
    mcp_config: str
    task_timeout: int = 3600
    idle_timeout: float = 300.0
    poll_interval: float = 1.0
    yolo: bool = True


def _build_argv(cfg: WorkerConfig, body: str) -> list[str]:
    argv = [cfg.claude_bin]
    if cfg.yolo:
        argv.append("--dangerously-skip-permissions")
    argv += ["--continue", "--mcp-config", cfg.mcp_config, "--print", body]
    return argv


def run_task(queue: TaskQueue, claimed: dict, cfg: WorkerConfig) -> None:
    """Run one claimed task to completion or failure."""
    tid = claimed["id"]
    if not _prepare_branch(queue, tid, claimed["body"], cfg.project_path):
        _finish(queue, tid, cfg)
        return
    argv = _build_argv(cfg, claimed["body"])
    logger.info("worker task %d: launching claude --continue", tid)
    proc = subprocess.Popen(
        argv, cwd=cfg.project_path, shell=False,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    queue.set_pid(tid, proc.pid)
    try:
        rc = proc.wait(timeout=cfg.task_timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        if _status(queue, tid) == "running":
            queue.mark_failed(tid, f"timeout after {cfg.task_timeout}s")
        _finish(queue, tid, cfg)
        return
    if _status(queue, tid) != "running":
        return  # cancelled externally; cancel path logs
    if rc == 0:
        queue.mark_done(tid)
    else:
        queue.mark_failed(tid, f"claude exited rc={rc}")
    log_task_finished(cfg.project_path, queue.get(tid) or {})


def _status(queue: TaskQueue, task_id: int) -> str:
    row = queue.get(task_id)
    return row["status"] if row else ""


def _finish(queue: TaskQueue, tid: int, cfg: "WorkerConfig") -> None:
    row = queue.get(tid) or {}
    log_task_finished(cfg.project_path, row)
    notify_task_done(cfg.db_path, row)


def _prepare_branch(queue: TaskQueue, tid: int, body: str, project_path: str) -> bool:
    """Create a per-task branch. Returns False if the task was marked failed.

    Non-git projects skip silently. Dirty repos refuse — protects the user's
    uncommitted work (they must commit/stash before running tasks here).
    """
    if not is_git_repo(project_path):
        logger.info("worker task %d: %s is not a git repo — running without branch", tid, project_path)
        return True
    clean, status = is_clean(project_path)
    if not clean:
        msg = f"repo dirty — commit or stash first:\n{status}"
        queue.mark_failed(tid, msg)
        logger.warning("worker task %d: %s", tid, msg)
        return False
    branch = task_branch_name(tid, body)
    ok, err = checkout_new_branch(project_path, branch)
    if not ok:
        queue.mark_failed(tid, f"could not create branch {branch}: {err}")
        logger.warning("worker task %d: checkout failed: %s", tid, err)
        return False
    queue.set_branch(tid, branch)
    logger.info("worker task %d: on branch %s", tid, branch)
    return True


def worker_loop(
    cfg: WorkerConfig,
    *, run_task_fn: Callable[[TaskQueue, dict, WorkerConfig], None] = run_task,
) -> None:
    """Drain the project's queue, then idle-exit."""
    queue = TaskQueue(cfg.db_path)
    last_task_at = time.monotonic()
    while True:
        claimed = queue.claim_next(cfg.project_path)
        if claimed is None:
            if time.monotonic() - last_task_at >= cfg.idle_timeout:
                logger.info("worker idle for %.1fs — exiting", cfg.idle_timeout)
                return
            time.sleep(cfg.poll_interval)
            continue
        run_task_fn(queue, claimed, cfg)
        last_task_at = time.monotonic()


def _cfg_from_env(project_path: str) -> WorkerConfig:
    return WorkerConfig(
        project_path=project_path,
        db_path=os.environ["CHAT_DB_PATH"],
        claude_bin=os.environ.get("CLAUDE_BIN", "claude"),
        mcp_config=os.environ["ROUTER_MCP_CONFIG"],
        task_timeout=int(os.environ.get("WORKER_TASK_TIMEOUT", "3600")),
        idle_timeout=float(os.environ.get("WORKER_IDLE_TIMEOUT", "300")),
        yolo=os.environ.get("CLAUDE_YOLO", "") == "1",
    )


if __name__ == "__main__":  # pragma: no cover
    if len(sys.argv) != 2:
        print("usage: python -m src.project_worker <project_path>", file=sys.stderr)
        sys.exit(2)
    logging.basicConfig(level=logging.INFO)
    worker_loop(_cfg_from_env(sys.argv[1]))
