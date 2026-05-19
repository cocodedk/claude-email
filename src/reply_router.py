"""Reply sub-classification + branch-reuse for email follow-ups.

Three routes (unchanged):
- reply_to_ask: original was a chat_ask → goes on the bus so the
  blocking chat_ask returns.
- reply_to_project: agent has a valid project_path under CLAUDE_CWD →
  queue the reply body as a task and ensure a worker is running.
- reply_bus_only: neither of the above → fall back to bus-only.

Branch-reuse layer: when the user replies on a thread we sent for a
task, walk In-Reply-To → outbound_emails.task_id → prior task to get
the prior branch_name. Guards: prior task must be in the same project,
and outbound.sender_agent must match agent_name. mutates_repo is
classified from the reply body so read-only follow-ups skip the dirty
check (Phase E's matrix).
"""
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from src.git_ops import current_branch, is_valid_task_branch, task_branch_name
from src.mutation_classifier import classify_mutation

logger = logging.getLogger(__name__)


@dataclass
class ReplyDecision:
    route: str   # "ask" | "project" | "bus"
    project_path: str = ""
    ack_subject_suffix: str = ""


def classify_reply(
    chat_db, agent_name: str, original_message_id: int, allowed_base: str,
) -> ReplyDecision:
    original = chat_db.get_message(original_message_id)
    if original is not None and original.get("type") == "ask":
        return ReplyDecision(route="ask")
    agent = chat_db.get_agent(agent_name)
    project_path = (agent or {}).get("project_path", "")
    if project_path and _project_in_base(project_path, allowed_base):
        return ReplyDecision(
            route="project",
            project_path=str(Path(project_path).resolve()),
        )
    return ReplyDecision(route="bus")


def _project_in_base(project_path: str, allowed_base: str) -> bool:
    if not allowed_base or not project_path:
        return False
    try:
        base = str(Path(allowed_base).resolve())
        resolved = str(Path(project_path).resolve())
    except OSError:
        return False
    if not os.path.isdir(resolved):
        return False
    return resolved == base or resolved.startswith(base + os.sep)


def _prior_branch(
    chat_db, task_queue, in_reply_to_header: str,
    project_path: str, agent_name: str,
) -> str:
    """Resolve a prior branch for branch-reuse on email follow-ups.

    Primary path: walk inbound In-Reply-To → outbound_emails.task_id →
    tasks.branch_name.

    Taskless-peer fallback: when the outbound row exists and the sender
    matches but has no task_id (peer agents like agent-em-backend send
    chat_notify outside any task context), fall back to the project's
    *current* git branch — but only if it has the canonical
    ``claude/task-<id>-<slug>`` shape per is_valid_task_branch. Non-task
    branches (master, feature/*, etc.) are rejected.

    Returns "" when any link is missing OR when sender_agent doesn't
    strictly equal agent_name (NULL fails too — fail closed) OR when
    the prior task is in a different project. Defense against misrouted
    replies inheriting the wrong branch."""
    if not in_reply_to_header or task_queue is None:
        return ""
    outbound = chat_db.find_outbound_email(in_reply_to_header)
    if not outbound:
        return ""
    if outbound.get("sender_agent") != agent_name:
        logger.info(
            "ignoring prior task: outbound sender_agent=%r != reply agent=%r",
            outbound.get("sender_agent"), agent_name,
        )
        return ""
    if not outbound.get("task_id"):
        branch = current_branch(project_path).strip()
        return branch if is_valid_task_branch(branch) else ""
    prior = task_queue.get(outbound["task_id"])
    if not prior:
        return ""
    if prior.get("project_path") != project_path:
        logger.info(
            "ignoring prior task: project mismatch (prior=%s, reply=%s)",
            prior.get("project_path"), project_path,
        )
        return ""
    branch = (prior.get("branch_name") or "").strip()
    # Defense-in-depth: a malformed branch_name on the prior row would
    # make _format_ack promise 'continue prior branch <garbage>' while
    # branch_prep quietly falls back to a fresh branch. Drop it here so
    # ACK and worker behavior stay consistent.
    if not branch or not is_valid_task_branch(branch):
        return ""
    return branch


def _format_ack(
    *, task_id: int, agent_name: str, worker_pid: int,
    prior_branch: str, mutates: bool | None, body: str,
) -> tuple[str, str]:
    """Return (ack_body, subject_tag). One of three sentences, chosen
    by actual outcome so the ACK never lies about whether a branch will
    exist.

    'continue prior branch' (not 'existing branch') is the round-3
    wording fix: the matrix in src.branch_prep may fall back to a fresh
    new branch if the prior was deleted between enqueue and worker run,
    and 'continue prior' stays accurate either way."""
    tag = f"Queued #{task_id}"
    if prior_branch:
        body_text = (
            f"Queued as task #{task_id} for {agent_name} to continue prior "
            f"branch `{prior_branch}` (worker pid {worker_pid})."
        )
    elif mutates is False:
        body_text = (
            f"Queued as task #{task_id} for {agent_name} as a read-only task "
            f"(no branch will be created; worker pid {worker_pid})."
        )
    else:
        branch = task_branch_name(task_id, body)
        body_text = (
            f"Queued as task #{task_id} for {agent_name} on planned branch "
            f"`{branch}` (worker pid {worker_pid})."
        )
    return body_text, tag


def apply_reply(
    chat_db, task_queue, worker_manager, *,
    agent_name: str, original_message_id: int,
    body: str, allowed_base: str,
    original_email_message_id: str = "",
) -> tuple[str, str]:
    """Record the reply and act on it. Returns (ack_body, subject_tag)."""
    decision = classify_reply(chat_db, agent_name, original_message_id, allowed_base)
    chat_db.insert_message(
        "user", agent_name, body, "reply", in_reply_to=original_message_id,
    )
    if decision.route == "project" and task_queue and worker_manager:
        prior_branch = _prior_branch(
            chat_db, task_queue, original_email_message_id,
            decision.project_path, agent_name,
        )
        mutates = classify_mutation(body)
        try:
            worker_pid = worker_manager.ensure_worker(decision.project_path)
            task_id = task_queue.enqueue(
                decision.project_path, body,
                branch_name=prior_branch,
                mutates_repo=mutates,
            )
        except ValueError as exc:
            logger.warning("Reply enqueue failed: %s", exc)
            return (
                f"Delivered to {agent_name} on the chat bus (couldn't queue: {exc}).",
                "Delivered",
            )
        return _format_ack(
            task_id=task_id, agent_name=agent_name, worker_pid=worker_pid,
            prior_branch=prior_branch, mutates=mutates, body=body,
        )
    if decision.route == "ask":
        return (
            f"Answer delivered to {agent_name} (was waiting on a question).",
            "Answer",
        )
    return (f"Delivered to {agent_name} on the chat bus.", "Delivered")
