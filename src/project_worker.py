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
        return
    if _status(queue, tid) != "running":
        return  # cancelled externally — don't overwrite
    if rc == 0:
        queue.mark_done(tid)
    else:
        queue.mark_failed(tid, f"claude exited rc={rc}")


def _status(queue: TaskQueue, task_id: int) -> str:
    row = queue.get(task_id)
    return row["status"] if row else ""


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
