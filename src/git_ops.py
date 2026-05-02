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


def commit_all(path: str, message: str) -> tuple[bool, str]:
    """Stage every change and commit with `message`.

    Returns (True, short_sha) on success; (False, error_text) otherwise.
    Used by chat_commit_project as an escape hatch when a dirty repo has
    blocked task execution — the user emails "commit these changes" and
    this clears the way without spinning up claude.
    """
    if not is_git_repo(path):
        return False, "not a git repository"
    rc, _, err = _git(["add", "-A"], path)
    if rc != 0:
        return False, err or f"git add rc={rc}"
    rc, _, err = _git(["diff", "--cached", "--quiet"], path)
    if rc == 0:
        return False, "nothing to commit — repo already clean"
    rc, _, err = _git(["commit", "-m", message, "--no-gpg-sign"], path)
    if rc != 0:
        return False, err or f"git commit rc={rc}"
    rc, sha, _ = _git(["rev-parse", "--short", "HEAD"], path)
    return (rc == 0, sha if rc == 0 else "committed (sha lookup failed)")


def push_current_branch(path: str) -> tuple[bool, str]:
    """Push the current branch to its tracking remote.

    Returns (True, stdout_or_summary) on success; (False, error_text)
    otherwise. Used by chat_commit_project when the user asks to commit
    AND push — keeps the LLM router off the chat_enqueue_task path that
    would otherwise create a fresh `claude/task-*` branch.
    """
    if not is_git_repo(path):
        return False, "not a git repository"
    rc, out, err = _git(["push"], path)
    if rc != 0:
        return False, err or out or f"git push rc={rc}"
    return True, out or "pushed"
