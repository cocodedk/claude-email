"""Ghost-task reaper — fix the 'running forever' silent-dead-state.

A worker process can die without updating its task row (SIGKILL, OOM,
host reboot mid-task, python traceback inside run_task before the
finally-finish). The task row stays `running`, its pid points at a dead
process, and no notification ever fires. chat_queue_status and
chat_where_am_i then show phantom work.

Each claude-email mail-loop tick calls sweep_ghosts(queue) per universe:
any running task whose `pid` is not alive gets marked failed with
"worker exited unexpectedly", the audit log + done-email fire, and the
queue is clean on the next poll.

pid=0 / None is treated as "not-yet-launched" and left alone (the worker
may still be in the branch-prep step).
"""
import logging

from src.process_liveness import is_alive
from src.task_log import log_task_finished
from src.task_notifier import notify_task_done
from src.task_queue import TaskQueue

logger = logging.getLogger(__name__)


def sweep_ghosts(queue: TaskQueue) -> int:
    """Reap any running task whose pid is dead. Returns count reaped."""
    reaped = 0
    for row in queue.list_running():
        pid = row.get("pid") or 0
        if pid <= 0:
            continue  # not yet launched
        if is_alive(pid):
            continue
        tid = row["id"]
        queue.mark_failed(
            tid, f"worker exited unexpectedly (pid {pid} gone); rc unknown",
        )
        refreshed = queue.get(tid) or row
        log_task_finished(row.get("project_path", ""), refreshed)
        notify_task_done(queue.path, refreshed)
        reaped += 1
        logger.warning("ghost reaper: task #%d (pid %d) marked failed", tid, pid)
    return reaped
