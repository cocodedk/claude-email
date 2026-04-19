"""Cancel-task + queue-status helpers for the email router.

cancel_running_task sends SIGTERM to the task's claude pid, waits up to
grace_seconds for it to exit, then escalates to SIGKILL. The DB row is
marked cancelled unconditionally so a never-PID-recorded claimed task is
still reachable. When drain_queue=True, pending tasks for the project are
also cancelled — a "stop everything for project X" verb.

queue_status returns a lightweight snapshot: the running task (with pid)
plus the pending list in dispatch order.
"""
import logging
import os
import signal
import time
from typing import Callable

from src.process_liveness import is_alive
from src.task_log import log_task_finished
from src.task_notifier import notify_task_done
from src.task_queue import TaskQueue

logger = logging.getLogger(__name__)


def _wait_for_exit(pid: int, grace_seconds: float) -> bool:
    """Poll until pid is gone or grace expires. True if the pid exited."""
    if pid <= 0:
        return True
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        if not is_alive(pid):
            return True
        time.sleep(0.1)
    return not is_alive(pid)


def cancel_running_task(
    queue: TaskQueue, project_path: str, *,
    drain_queue: bool = False,
    grace_seconds: float = 10.0,
    wait_fn: Callable[[int, float], bool] | None = None,
) -> dict:
    if wait_fn is None:
        wait_fn = _wait_for_exit
    running = queue.get_running(project_path)
    if running is None:
        result = {"status": "idle"}
        if drain_queue:
            result["drained"] = queue.drain_pending(project_path)
        return result
    pid = running.get("pid") or 0
    if pid > 0:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pid = 0
        if pid > 0 and not wait_fn(pid, grace_seconds):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    queue.cancel(running["id"])
    row = queue.get(running["id"]) or {}
    log_task_finished(project_path, row)
    notify_task_done(queue.path, row)
    result = {"status": "cancelled", "task_id": running["id"]}
    if drain_queue:
        result["drained"] = queue.drain_pending(project_path)
    return result


def queue_status(queue: TaskQueue, project_path: str) -> dict:
    running = queue.get_running(project_path)
    pending = queue.list_pending(project_path)
    return {"running": running, "pending": pending}
