"""Chat-specific email handlers — extracted from main.py to stay under 200 lines."""
import logging
import subprocess

from src.chat_db import ChatDB
from src.chat_router import Route, classify_email
from src.executor import extract_command
from src.mailer import send_reply
from src.spawner import spawn_agent

logger = logging.getLogger(__name__)


def _send_reply(config: dict, original_message, body: str) -> None:
    """Send an email reply to the authorized sender, threading on the original."""
    subject = original_message.get("Subject", "command")
    msg_id = original_message.get("Message-ID", "")

    send_reply(
        smtp_host=config["smtp_host"],
        smtp_port=config["smtp_port"],
        username=config["username"],
        password=config["password"],
        to=config["authorized_sender"],
        subject=subject,
        body=body,
        in_reply_to=msg_id,
        references=msg_id,
        email_domain=config.get("email_domain", ""),
    )


def handle_chat_email(message, config: dict, chat_db: ChatDB) -> bool:
    """Route an email through the chat system. Returns True if handled.

    Returns False for CLI-fallback so the caller can run the normal path.
    """
    route = classify_email(message, chat_db, auth_prefix=config["auth_prefix"])

    if route.kind == "chat_reply":
        body = extract_command(message)
        chat_db.insert_message(
            "user", route.agent_name, body, "reply",
            in_reply_to=route.original_message_id,
        )
        logger.info("Chat reply routed to %s", route.agent_name)
        return True

    if route.kind == "agent_command":
        chat_db.insert_message("user", route.agent_name, route.body, "command")
        _send_reply(config, message, f"Command dispatched to {route.agent_name}")
        logger.info("Agent command dispatched to %s", route.agent_name)
        return True

    if route.kind == "meta":
        _handle_meta(route, config, message, chat_db)
        return True

    return False


def _handle_meta(route: Route, config: dict, message, chat_db: ChatDB) -> None:
    """Handle meta-commands: status, spawn, restart."""
    if route.meta_command == "status":
        agents = chat_db.list_agents()
        if not agents:
            body = "No agents registered."
        else:
            lines = [f"{a['name']}  {a['status']}  {a['project_path']}" for a in agents]
            body = "\n".join(lines)
        _send_reply(config, message, body)

    elif route.meta_command == "spawn":
        parts = route.meta_args.split(None, 1)
        project_dir = parts[0] if parts else ""
        instruction = parts[1] if len(parts) > 1 else ""
        if not project_dir:
            _send_reply(config, message, "Usage: spawn <path> [instruction]")
            return
        try:
            name, pid = spawn_agent(
                chat_db, project_dir, config["chat_url"],
                instruction=instruction,
                claude_bin=config["claude_bin"],
                allowed_base=config.get("claude_cwd"),
            )
        except ValueError as exc:
            _send_reply(config, message, f"Spawn rejected: {exc}")
            return
        _send_reply(config, message, f"Spawned {name} (PID {pid})")

    elif route.meta_command == "restart":
        target = route.meta_args.strip().lower()
        if target == "chat":
            svc = config["service_name_chat"]
            subprocess.run(
                ["systemctl", "--user", "restart", svc],
                shell=False, check=False,
            )
            _send_reply(config, message, f"Restarted {svc}")
        elif target == "self":
            svc = config["service_name_email"]
            # No reply — service will restart before it can send
            subprocess.run(
                ["systemctl", "--user", "restart", svc],
                shell=False, check=False,
            )
        else:
            _send_reply(config, message, f"Unknown restart target: {target}")


def relay_outbound_messages(config: dict, chat_db: ChatDB) -> None:
    """Pick up pending agent-to-user messages and send them as emails."""
    pending = chat_db.get_pending_messages_for("user")
    for msg in pending:
        subject = f"[{msg['from_name']}] {msg['body'][:60]}"
        prev_email_id = chat_db.get_last_email_message_id_for_agent(msg["from_name"]) or ""
        email_msg_id = send_reply(
            smtp_host=config["smtp_host"],
            smtp_port=config["smtp_port"],
            username=config["username"],
            password=config["password"],
            to=config["authorized_sender"],
            subject=subject,
            body=msg["body"],
            in_reply_to=prev_email_id,
            references=prev_email_id,
            email_domain=config.get("email_domain", ""),
        )
        chat_db.mark_message_delivered(msg["id"])
        if email_msg_id:
            chat_db.set_email_message_id(msg["id"], email_msg_id)
        logger.info("Relayed message %d from %s to user", msg["id"], msg["from_name"])
