"""Per-sender dispatch helpers — universe resource build + routing.

Kept separate from main.py so run_loop stays compact (200-line cap) while
the universe machinery grows as more sender-scoped state lands.
"""
import logging

from src.chat_db import ChatDB
from src.security import identify_sender
from src.task_queue import TaskQueue
from src.universes import Universe
from src.worker_manager import WorkerManager

logger = logging.getLogger(__name__)


def build_universe_resources(universes) -> dict:
    """Return {sender_lower: (universe, ChatDB, TaskQueue, WorkerManager)}."""
    res = {}
    for u in universes:
        cdb = ChatDB(u.chat_db_path)
        tq = TaskQueue(u.chat_db_path)
        wm = WorkerManager(db_path=u.chat_db_path, project_root=u.allowed_base)
        res[u.sender.lower()] = (u, cdb, tq, wm)
    return res


def universes_from_config(config: dict) -> list:
    """Return config['universes'] when present; else synthesize a single-
    universe list from the legacy flat fields. Preserves back-compat with
    tests and callers that pre-date the multi-universe refactor."""
    if config.get("universes"):
        return list(config["universes"])
    return [Universe(
        sender=config.get("authorized_sender", ""),
        allowed_base=config.get("claude_cwd", ""),
        chat_db_path=config.get("chat_db_path", ""),
        chat_url=config.get("chat_url", ""),
        mcp_config=config.get("mcp_config", ""),
        service_name_chat=config.get("service_name_chat", ""),
    )]


def dispatch_by_sender(msg, config: dict, resources: dict, process_email) -> None:
    """Pick the matching universe and invoke process_email scoped to it.

    When no authorized sender matches, fall through to process_email with
    the unscoped config so the rejection path logs centrally.
    """
    sender = identify_sender(msg, config["authorized_senders"])
    if sender is None:
        process_email(msg, config)
        return
    universe, cdb, tq, wm = resources[sender]
    scoped = {**config, "_universe": universe, "claude_cwd": universe.allowed_base}
    process_email(msg, scoped, chat_db=cdb, task_queue=tq, worker_manager=wm)
