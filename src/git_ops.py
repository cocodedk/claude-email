"""Git helpers used by the per-task branch strategy.

Kept focused: is_git_repo, is_clean, checkout_new_branch, slugify.
Every subprocess call is shell=False. Non-git projects return
consistent tuples so callers can skip gracefully instead of crashing.
"""
import re
import subprocess


def _git(args: list[str], cwd: str) -> tuple[int, str, str]:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, shell=False, check=False,
        capture_output=True, text=True,
    )
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def is_git_repo(path: str) -> bool:
    rc, _, _ = _git(["rev-parse", "--is-inside-work-tree"], path)
    return rc == 0


def is_clean(path: str) -> tuple[bool, str]:
    """Return (True, '') if clean; (False, status_text) otherwise."""
    rc, out, err = _git(["status", "--porcelain"], path)
    if rc != 0:
        return False, err or f"git status rc={rc}"
    return (not out, out)


def current_branch(path: str) -> str:
    rc, out, _ = _git(["rev-parse", "--abbrev-ref", "HEAD"], path)
    return out if rc == 0 else ""


def checkout_new_branch(path: str, branch_name: str) -> tuple[bool, str]:
    """Create + switch to a new branch. Returns (success, error_text)."""
    rc, _, err = _git(["checkout", "-b", branch_name], path)
    return (rc == 0, err if rc != 0 else "")


_SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")


def slugify(body: str, max_len: int = 40) -> str:
    """Short, branch-name-safe slug from a task body.

    Strips non-alphanumerics to hyphens, lowercases, trims to max_len,
    avoids trailing hyphens. Returns 'task' when body has nothing usable.
    """
    slug = _SLUG_RE.sub("-", body).strip("-").lower()[:max_len].rstrip("-")
    return slug or "task"


def task_branch_name(task_id: int, body: str) -> str:
    return f"claude/task-{task_id}-{slugify(body)}"
