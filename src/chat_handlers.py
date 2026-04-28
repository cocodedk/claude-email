"""Chat-specific email handlers — extracted from main.py to stay under 200 lines.

Outbound relay and DB cleanup live in ``src/chat_relay.py`` (re-exported
below for back-compat with ``from src.chat_handlers import`` callers).
"""
import logging
import subprocess

from src.chat_db import ChatDB
from src.chat_relay import (  # noqa: F401 — re-export for back-compat
    maybe_cleanup_db, relay_outbound_messages,
)
from src.chat_router import Route, classify_email
from src.email_format import prepend_tag, with_footer
from src.executor import extract_command
from src.mailer import send_reply
from src.reply_router import apply_reply
from src.spawner import spawn_agent
from src.task_queue import TaskQueue
from src.worker_manager import WorkerManager

logger = logging.getLogger(__name__)


def send_threaded_reply(
    config: dict, original_message, body: str,
    tag: str | None = None, footer: bool = True,
    chat_db: ChatDB | None = None, kind: str = "ack",
    sender_agent: str = "",
) -> str:
    """Send an email reply to the authorized sender, threading on the original.

    tag prepends `[tag]` to the subject; footer appends the universal
    "what you can do" hint. Both default on for system-originated messages
    (ACKs) so the inbox stays self-documenting.

    When ``chat_db`` is provided, the SMTP Message-ID is persisted into
    ``outbound_emails`` so the user's reply on this thread auto-auths via
    ``security.is_authorized``'s thread-match path. Callers without a
    chat_db (legacy / test paths) still send mail but produce a row that
    won't auth replies — so always pass chat_db when one is available.
    """
    subject = prepend_tag(original_message.get("Subject", "command"), tag)
    msg_id = original_message.get("Message-ID", "")
    sent_id = send_reply(
        smtp_host=config["smtp_host"], smtp_port=config["smtp_port"],
        username=config["username"], password=config["password"],
        to=config["authorized_sender"], subject=subject,
        body=with_footer(body, footer),
        in_reply_to=msg_id, references=msg_id,
        email_domain=config.get("email_domain", ""),
    )
    if chat_db is not None and sent_id:
        chat_db.record_outbound_email(
            sent_id, kind=kind, sender_agent=sender_agent,
        )
    return sent_id


def handle_chat_email(
    message, config: dict, chat_db: ChatDB,
    task_queue: TaskQueue | None = None,
    worker_manager: WorkerManager | None = None,
) -> bool:
    """Route an email through the chat system. Returns True if handled.

    Returns False for CLI-fallback so the caller can run the normal path.
    """
    route = classify_email(message, chat_db, auth_prefix=config["auth_prefix"])

    if route.kind == "chat_reply":
        _handle_reply(route, message, config, chat_db, task_queue, worker_manager)
        return True

    if route.kind == "agent_command":
        # Reject unknown targets. Agents register themselves at SessionStart
        # (chat-register-self.py hook), so a missing row means a typo, not a
        # valid not-yet-started agent. Silently queueing would hide the typo
        # — the user gets a "Dispatched" ack, the message sits pending for
        # a phantom inbox, and the wake-watcher polls it forever.
        if chat_db.get_agent(route.agent_name) is None:
            known = [a["name"] for a in chat_db.list_agents()]
            hint = ", ".join(known) if known else "(none registered)"
            send_threaded_reply(
                config, message,
                f"Unknown agent {route.agent_name}. Known agents: {hint}",
                tag="Error", chat_db=chat_db, kind="error",
            )
            logger.warning("Agent command rejected — %s not registered", route.agent_name)
            return True
        chat_db.insert_message("user", route.agent_name, route.body, "command")
        send_threaded_reply(
            config, message, f"Command dispatched to {route.agent_name}",
            tag="Dispatched", chat_db=chat_db, kind="ack",
            sender_agent=route.agent_name,
        )
        logger.info("Agent command dispatched to %s", route.agent_name)
        return True

    if route.kind == "meta":
        _handle_meta(route, config, message, chat_db)
        return True

    return False


def _handle_reply(
    route, message, config: dict, chat_db: ChatDB,
    task_queue: TaskQueue | None, worker_manager: WorkerManager | None,
) -> None:
    body = extract_command(message, strip_secret=config.get("shared_secret", ""))
    ack, tag = apply_reply(
        chat_db, task_queue, worker_manager,
        agent_name=route.agent_name,
        original_message_id=route.original_message_id,
        body=body, allowed_base=config.get("claude_cwd") or "",
    )
    logger.info("Reply routed: %s", ack)
    send_threaded_reply(
        config, message, ack, tag=tag, chat_db=chat_db, kind="reply_ack",
        sender_agent=route.agent_name,
    )


def _handle_meta(route: Route, config: dict, message, chat_db: ChatDB) -> None:
    """Handle meta-commands: status, spawn, restart."""
    if route.meta_command == "status":
        agents = chat_db.list_agents()
        if not agents:
            body = "No agents registered."
        else:
            lines = [f"{a['name']}  {a['status']}  {a['project_path']}" for a in agents]
            body = "\n".join(lines)
        send_threaded_reply(
            config, message, body, tag="Status", chat_db=chat_db, kind="status",
        )

    elif route.meta_command == "spawn":
        parts = route.meta_args.split(None, 1)
        project_dir = parts[0] if parts else ""
        instruction = parts[1] if len(parts) > 1 else ""
        if not project_dir:
            send_threaded_reply(
                config, message, "Usage: spawn <name-or-path> [instruction]",
                tag="Error", chat_db=chat_db, kind="error",
            )
            return
        try:
            name, pid = spawn_agent(
                chat_db, project_dir, config["chat_url"], instruction=instruction,
                claude_bin=config["claude_bin"],
                allowed_base=config.get("claude_cwd"),
                yolo=config.get("claude_yolo", False),
                extra_env=config.get("claude_extra_env") or None,
                model=config.get("claude_model"), effort=config.get("claude_effort"),
                max_budget_usd=config.get("claude_max_budget_usd"),
            )
        except ValueError as exc:
            send_threaded_reply(
                config, message, f"Spawn rejected: {exc}",
                tag="Error", chat_db=chat_db, kind="error",
            )
            return
        send_threaded_reply(
            config, message, f"Spawned {name} (PID {pid})",
            tag="Spawned", chat_db=chat_db, kind="ack", sender_agent=name,
        )

    elif route.meta_command == "restart":
        target = route.meta_args.strip().lower()
        if target == "chat":
            svc = config["service_name_chat"]
            subprocess.run(["systemctl", "--user", "restart", svc], shell=False, check=False)
            send_threaded_reply(
                config, message, f"Restarted {svc}",
                tag="Restarted", chat_db=chat_db, kind="ack",
            )
        elif target == "self":
            svc = config["service_name_email"]
            subprocess.run(["systemctl", "--user", "restart", svc], shell=False, check=False)
        else:
            send_threaded_reply(
                config, message, f"Unknown restart target: {target}",
                tag="Error", chat_db=chat_db, kind="error",
            )


