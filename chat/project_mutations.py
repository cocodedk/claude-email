"""MCP tools that mutate a project repo's working tree:
chat_reset_project / chat_confirm_reset / chat_commit_project.

Split from ``chat/project_tools.py`` to keep both files under the
200-line cap. Shares ``_resolve_project`` from project_tools.
"""
from chat.project_tools import _resolve_project
from src.git_ops import commit_all, push_current_branch
from src.reset_control import TokenStore, perform_reset
from src.task_queue import TaskQueue


def reset_project_tool(
    tokens: TokenStore, *, project: str, allowed_base: str,
) -> dict:
    """Step 1 of the two-step hard reset — issues a confirm token."""
    try:
        resolved = _resolve_project(project, allowed_base)
    except ValueError as exc:
        return {"error": str(exc)}
    token = tokens.issue(resolved)
    return {
        "status": "confirm_required",
        "confirm_token": token,
        "project": resolved,
        "instruction": (
            "Reset will cancel the running task, drain the queue, and run "
            "`git reset --hard HEAD && git clean -fd`. To confirm, call "
            "chat_confirm_reset with the same project and this token."
        ),
    }


def confirm_reset_tool(
    queue: TaskQueue, tokens: TokenStore, *,
    project: str, token: str, allowed_base: str,
) -> dict:
    """Step 2 — validate token and perform the destructive reset."""
    try:
        resolved = _resolve_project(project, allowed_base)
    except ValueError as exc:
        return {"error": str(exc)}
    if not tokens.consume(resolved, token):
        return {"error": "invalid or expired confirm token"}
    return perform_reset(queue, resolved)


def commit_project_tool(
    *, project: str, message: str, allowed_base: str, push: bool = False,
) -> dict:
    """Commit any pending changes in a project — escape hatch for dirty
    repos. With ``push=True``, also runs ``git push`` so 'commit and push'
    emails map to one tool call (else the LLM router falls through to
    chat_enqueue_task and creates a fresh per-task branch).
    """
    try:
        resolved = _resolve_project(project, allowed_base)
    except ValueError as exc:
        return {"error": str(exc)}
    ok, detail = commit_all(resolved, message)
    if not ok:
        return {"error": detail}
    if not push:
        return {"status": "committed", "sha": detail, "project": resolved}
    push_ok, push_detail = push_current_branch(resolved)
    if not push_ok:
        return {
            "status": "committed",
            "sha": detail,
            "project": resolved,
            "push_error": push_detail,
        }
    return {
        "status": "committed-and-pushed",
        "sha": detail,
        "project": resolved,
        "push": push_detail,
    }
