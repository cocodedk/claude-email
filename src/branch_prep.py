"""Per-task branch preparation — the nine-cell matrix.

Decisions (in order, return as soon as one fires):
  1. Not a git repo                              → no branch, succeed.
  2. mutates_repo == False AND no valid prior    → skip dirty check + no
                                                    branch, succeed.
  3. Has a valid prior branch:
       3a. already on prior                      → succeed (allow dirty,
                                                    this is OUR work).
       3b. not on prior + dirty                  → fail (can't switch).
       3c. not on prior + clean + branch exists  → checkout existing.
       3d. not on prior + clean + branch missing → fresh new branch.
  4. Mutating, no valid prior, repo clean        → new branch.
  5. Mutating, no valid prior, repo dirty        → fail.

The mutates_repo column may be NULL (unknown). NULL is treated as
mutating so existing rows and ambiguous-classifier rows stay
safety-gated.
"""
import logging

from src.git_ops import (
    branch_exists, checkout_existing_branch, checkout_new_branch,
    current_branch, is_clean, is_git_repo, is_valid_task_branch,
    task_branch_name,
)

logger = logging.getLogger(__name__)


def _is_mutating(task: dict) -> bool:
    """NULL or 1 → mutating; only an explicit 0/False is read-only."""
    v = task.get("mutates_repo")
    if v is None:
        return True
    return bool(v)


def _valid_prior(task: dict) -> str:
    """Return the prior branch_name if it's set AND valid; else ''."""
    name = (task.get("branch_name") or "").strip()
    if name and is_valid_task_branch(name):
        return name
    if name:
        logger.warning("ignoring invalid prior branch_name: %r", name)
    return ""


def prepare_branch(queue, task: dict, project_path: str) -> bool:
    tid = task["id"]

    if not is_git_repo(project_path):
        logger.info(
            "worker task %d: %s is not a git repo — running without branch",
            tid, project_path,
        )
        return True

    prior = _valid_prior(task)

    if not prior and not _is_mutating(task):
        logger.info(
            "worker task %d: read-only, no prior branch — skipping dirty "
            "check and branch creation", tid,
        )
        return True

    if prior:
        return _handle_prior(queue, task, project_path, prior)

    return _new_branch(queue, task, project_path)


def _handle_prior(
    queue, task: dict, project_path: str, prior: str,
) -> bool:
    tid = task["id"]
    current = current_branch(project_path)
    if current == prior:
        logger.info(
            "worker task %d: already on prior branch %s — continuing",
            tid, prior,
        )
        return True

    clean, status = is_clean(project_path)
    if not clean:
        msg = (
            f"repo dirty on '{current or 'detached HEAD'}', cannot switch "
            f"to prior branch '{prior}' safely; commit or stash first:\n"
            f"{status}"
        )
        queue.mark_failed(tid, msg)
        logger.warning("worker task %d: %s", tid, msg)
        return False

    if not branch_exists(project_path, prior):
        logger.info(
            "worker task %d: prior branch %s missing — creating fresh",
            tid, prior,
        )
        return _new_branch(queue, task, project_path)

    ok, err = checkout_existing_branch(project_path, prior)
    if not ok:
        queue.mark_failed(
            tid, f"could not checkout existing branch {prior}: {err}",
        )
        logger.warning(
            "worker task %d: checkout existing failed: %s", tid, err,
        )
        return False
    logger.info("worker task %d: reusing branch %s", tid, prior)
    return True


def _new_branch(queue, task: dict, project_path: str) -> bool:
    tid = task["id"]
    clean, status = is_clean(project_path)
    if not clean:
        msg = f"repo dirty — commit or stash first:\n{status}"
        queue.mark_failed(tid, msg)
        logger.warning("worker task %d: %s", tid, msg)
        return False
    branch = task_branch_name(tid, task["body"])
    ok, err = checkout_new_branch(project_path, branch)
    if not ok:
        queue.mark_failed(tid, f"could not create branch {branch}: {err}")
        logger.warning("worker task %d: checkout failed: %s", tid, err)
        return False
    queue.set_branch(tid, branch)
    logger.info("worker task %d: on branch %s", tid, branch)
    return True
