"""Chat-specific email handlers — extracted from main.py to stay under 200 lines."""
import logging
import smtplib
import subprocess
import time

from src.chat_db import ChatDB
from src.chat_router import Route, classify_email
from src.executor import extract_command
from src.mailer import send_reply
from src.spawner import spawn_agent

logger = logging.getLogger(__name__)

_PERMANENT_SMTP_ERRORS = (
    smtplib.SMTPRecipientsRefused,
    smtplib.SMTPSenderRefused,
    smtplib.SMTPAuthenticationError,
    smtplib.SMTPHeloError,
    smtplib.SMTPNotSupportedError,
)

_CLEANUP_INTERVAL_SECONDS = 86400
_CLEANUP_RETENTION_DAYS = 30
_last_cleanup_ts = 0.0


def send_threaded_reply(config: dict, original_message, body: str) -> str:
    """Send an email reply to the authorized sender, threading on the original.

    Returns the Message-ID of the sent email.
    """
    subject = original_message.get("Subject", "command")
    msg_id = original_message.get("Message-ID", "")

    return send_reply(
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
        send_threaded_reply(config, message, f"Command dispatched to {route.agent_name}")
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
        send_threaded_reply(config, message, body)

    elif route.meta_command == "spawn":
        parts = route.meta_args.split(None, 1)
        project_dir = parts[0] if parts else ""
        instruction = parts[1] if len(parts) > 1 else ""
        if not project_dir:
            send_threaded_reply(config, message, "Usage: spawn <name-or-path> [instruction]")
            return
        try:
            name, pid = spawn_agent(
                chat_db, project_dir, config["chat_url"],
                instruction=instruction,
                claude_bin=config["claude_bin"],
                allowed_base=config.get("claude_cwd"),
                yolo=config.get("claude_yolo", False),
                extra_env=config.get("claude_extra_env") or None,
                model=config.get("claude_model"),
                effort=config.get("claude_effort"),
                max_budget_usd=config.get("claude_max_budget_usd"),
            )
        except ValueError as exc:
            send_threaded_reply(config, message, f"Spawn rejected: {exc}")
            return
        send_threaded_reply(config, message, f"Spawned {name} (PID {pid})")

    elif route.meta_command == "restart":
        target = route.meta_args.strip().lower()
        if target == "chat":
            svc = config["service_name_chat"]
            subprocess.run(
                ["systemctl", "--user", "restart", svc],
                shell=False, check=False,
            )
            send_threaded_reply(config, message, f"Restarted {svc}")
        elif target == "self":
            svc = config["service_name_email"]
            # No reply — service will restart before it can send
            subprocess.run(
                ["systemctl", "--user", "restart", svc],
                shell=False, check=False,
            )
        else:
            send_threaded_reply(config, message, f"Unknown restart target: {target}")


def relay_outbound_messages(config: dict, chat_db: ChatDB) -> None:
    """Pick up pending agent-to-user messages and send them as emails.

    On permanent SMTP errors, the message is marked failed so it won't be
    retried forever. On transient errors, it stays pending and we stop
    iterating to avoid hammering a broken connection.
    """
    pending = chat_db.get_pending_messages_for("user")
    for msg in pending:
        subject = f"[{msg['from_name']}] message"
        prev_email_id = chat_db.get_last_email_message_id_for_agent(msg["from_name"]) or ""
        try:
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
        except _PERMANENT_SMTP_ERRORS as exc:
            logger.error("Permanent SMTP error relaying message %d: %s — marking failed", msg["id"], exc)
            chat_db.mark_message_failed(msg["id"])
            continue
        except (smtplib.SMTPException, OSError) as exc:
            logger.warning("Transient SMTP error relaying message %d: %s — will retry", msg["id"], exc)
            return
        if email_msg_id:
            chat_db.set_email_message_id(msg["id"], email_msg_id)
        chat_db.mark_message_delivered(msg["id"])
        logger.info("Relayed message %d from %s to user", msg["id"], msg["from_name"])


def maybe_cleanup_db(chat_db: ChatDB) -> None:
    """Prune old delivered/failed messages + events once per day."""
    global _last_cleanup_ts
    now = time.time()
    if now - _last_cleanup_ts < _CLEANUP_INTERVAL_SECONDS:
        return
    _last_cleanup_ts = now
    try:
        counts = chat_db.cleanup_old(days=_CLEANUP_RETENTION_DAYS)
        if counts["messages"] or counts["events"]:
            logger.info(
                "DB cleanup: removed %d messages, %d events",
                counts["messages"], counts["events"],
            )
    except Exception:
        logger.exception("DB cleanup failed")
