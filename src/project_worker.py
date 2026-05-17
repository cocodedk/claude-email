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

from src.branch_prep import prepare_branch
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


_PLAN_FIRST_PREFIX = (
    "BEFORE doing any actual work for this task, propose a short plan "
    "(3-6 sentences) to the user via "
    "mcp__claude-chat__chat_ask(_caller=\"agent-<project>\", message=\"...\") "
    "and WAIT for their reply. Only start coding AFTER the user approves "
    "(or refines) the plan. If they say stop/cancel, just reply briefly "
    "via chat_notify and exit without modifying anything.\n\n"
    "Task to propose a plan for:\n"
)


def _build_argv(cfg: WorkerConfig, body: str, plan_first: bool = False) -> list[str]:
    argv = [cfg.claude_bin]
    if cfg.yolo:
        argv.append("--dangerously-skip-permissions")
    final_body = (_PLAN_FIRST_PREFIX + body) if plan_first else body
    argv += ["--continue", "--mcp-config", cfg.mcp_config, "--print", final_body]
    return argv


def run_task(queue: TaskQueue, claimed: dict, cfg: WorkerConfig) -> None:
    """Run one claimed task. Captures stdout+stderr (merged); last ~4KB
    lands on task.output_text so the done-email has real context."""
    tid = claimed["id"]
    if not prepare_branch(queue, claimed, cfg.project_path):
        _finish(queue, tid, cfg)
        return
    argv = _build_argv(cfg, claimed["body"], plan_first=bool(claimed.get("plan_first")))
    logger.info("worker task %d: launching claude --continue", tid)
    proc = subprocess.Popen(
        argv, cwd=cfg.project_path, shell=False,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    queue.set_pid(tid, proc.pid)
    try:
        stdout, _ = proc.communicate(timeout=cfg.task_timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, _ = proc.communicate()
        queue.set_output(tid, _tail(stdout))
        if _status(queue, tid) == "running":
            queue.mark_failed(tid, f"timeout after {cfg.task_timeout}s")
        _finish(queue, tid, cfg)
        return
    rc = proc.returncode
    queue.set_output(tid, _tail(stdout))
    if _status(queue, tid) != "running":
        return  # cancelled externally; cancel path logs
    if rc == 0:
        queue.mark_done(tid)
    else:
        queue.mark_failed(tid, f"claude exited rc={rc}; see output_text tail")
    _finish(queue, tid, cfg)


_MAX_OUTPUT_BYTES = 4_000


def _tail(s: str | None) -> str:
    if not s:
        return ""
    encoded = s.encode("utf-8", errors="replace")
    if len(encoded) <= _MAX_OUTPUT_BYTES:
        return s
    return "…(truncated)…\n" + encoded[-_MAX_OUTPUT_BYTES:].decode("utf-8", errors="replace")


def _status(queue: TaskQueue, task_id: int) -> str:
    row = queue.get(task_id)
    return row["status"] if row else ""


def _finish(queue: TaskQueue, tid: int, cfg: "WorkerConfig") -> None:
    row = queue.get(tid) or {}
    log_task_finished(cfg.project_path, row)
    notify_task_done(cfg.db_path, row)


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
