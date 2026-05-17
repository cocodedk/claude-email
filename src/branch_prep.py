"""Per-task branch preparation — extracted from project_worker.

Lives in its own module so the worker stays under 200 lines and the
nine-cell matrix has room for focused tests.

Behavior in this revision is byte-identical to the deleted inline
_prepare_branch. Task E.9 in the implementation plan adds the
mutates_repo + branch_name + current_branch matrix."""
import logging

from src.git_ops import (
    checkout_new_branch, is_clean, is_git_repo, task_branch_name,
)

logger = logging.getLogger(__name__)


def prepare_branch(queue, task: dict, project_path: str) -> bool:
    """Create a per-task branch. Returns False if the task was marked failed.

    Non-git projects skip silently. Dirty repos refuse — protects the
    user's uncommitted work."""
    tid = task["id"]
    body = task["body"]
    if not is_git_repo(project_path):
        logger.info(
            "worker task %d: %s is not a git repo — running without branch",
            tid, project_path,
        )
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
