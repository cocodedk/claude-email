"""Reply sub-classification: decide how a user's email reply should act.

Three routes:
- reply_to_ask: the original agent message was a chat_ask — answer goes on
  the bus so the blocking chat_ask tool returns. Legacy behavior.
- reply_to_project: the agent has a valid project_path under CLAUDE_CWD —
  queue the reply body as a task and make sure a worker is running. The
  worker's claude --continue brings full session memory, so the answer
  follows the thread of work.
- reply_bus_only: neither of the above (e.g. reply to an agent that was
  never registered with a project) — fall back to the old bus-only path
  so the user at least sees something in the DB.
"""
import logging
import os
from dataclasses import dataclass
from pathlib import Path

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


def apply_reply(
    chat_db, task_queue, worker_manager, *,
    agent_name: str, original_message_id: int,
    body: str, allowed_base: str,
) -> tuple[str, str]:
    """Record the reply and act on it. Returns (ack_body, subject_tag)."""
    decision = classify_reply(chat_db, agent_name, original_message_id, allowed_base)
    chat_db.insert_message(
        "user", agent_name, body, "reply", in_reply_to=original_message_id,
    )
    if decision.route == "project" and task_queue and worker_manager:
        try:
            worker_pid = worker_manager.ensure_worker(decision.project_path)
            task_id = task_queue.enqueue(decision.project_path, body)
        except ValueError as exc:
            logger.warning("Reply enqueue failed: %s", exc)
            return (
                f"Delivered to {agent_name} on the chat bus (couldn't queue: {exc}).",
                "Delivered",
            )
        return (
            f"Queued as task #{task_id} for {agent_name} (worker pid {worker_pid}).",
            f"Queued #{task_id}",
        )
    if decision.route == "ask":
        return (
            f"Answer delivered to {agent_name} (was waiting on a question).",
            "Answer",
        )
    return (f"Delivered to {agent_name} on the chat bus.", "Delivered")
