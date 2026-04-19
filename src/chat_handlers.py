"""Chat-specific email handlers — extracted from main.py to stay under 200 lines."""
import logging
import smtplib
import subprocess
import time

from src.chat_db import ChatDB
from src.chat_router import Route, classify_email
from src.email_format import prepend_tag, tag_for_message_type, with_footer
from src.executor import extract_command
from src.mailer import send_reply
from src.relay_routing import recipient_for_message, thread_id_for_message
from src.reply_router import apply_reply
from src.spawner import spawn_agent
from src.task_queue import TaskQueue
from src.worker_manager import WorkerManager

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


def send_threaded_reply(
    config: dict, original_message, body: str,
    tag: str | None = None, footer: bool = True,
) -> str:
    """Send an email reply to the authorized sender, threading on the original.

    tag prepends `[tag]` to the subject; footer appends the universal
    "what you can do" hint. Both default on for system-originated messages
    (ACKs) so the inbox stays self-documenting.
    """
    subject = prepend_tag(original_message.get("Subject", "command"), tag)
    msg_id = original_message.get("Message-ID", "")
    return send_reply(
        smtp_host=config["smtp_host"], smtp_port=config["smtp_port"],
        username=config["username"], password=config["password"],
        to=config["authorized_sender"], subject=subject,
        body=with_footer(body, footer),
        in_reply_to=msg_id, references=msg_id,
        email_domain=config.get("email_domain", ""),
    )


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
        chat_db.insert_message("user", route.agent_name, route.body, "command")
        send_threaded_reply(
            config, message, f"Command dispatched to {route.agent_name}",
            tag="Dispatched",
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
    send_threaded_reply(config, message, ack, tag=tag)


def _handle_meta(route: Route, config: dict, message, chat_db: ChatDB) -> None:
    """Handle meta-commands: status, spawn, restart."""
    if route.meta_command == "status":
        agents = chat_db.list_agents()
        if not agents:
            body = "No agents registered."
        else:
            lines = [f"{a['name']}  {a['status']}  {a['project_path']}" for a in agents]
            body = "\n".join(lines)
        send_threaded_reply(config, message, body, tag="Status")

    elif route.meta_command == "spawn":
        parts = route.meta_args.split(None, 1)
        project_dir = parts[0] if parts else ""
        instruction = parts[1] if len(parts) > 1 else ""
        if not project_dir:
            send_threaded_reply(config, message, "Usage: spawn <name-or-path> [instruction]", tag="Error")
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
            send_threaded_reply(config, message, f"Spawn rejected: {exc}", tag="Error")
            return
        send_threaded_reply(config, message, f"Spawned {name} (PID {pid})", tag="Spawned")

    elif route.meta_command == "restart":
        target = route.meta_args.strip().lower()
        if target == "chat":
            svc = config["service_name_chat"]
            subprocess.run(["systemctl", "--user", "restart", svc], shell=False, check=False)
            send_threaded_reply(config, message, f"Restarted {svc}", tag="Restarted")
        elif target == "self":
            svc = config["service_name_email"]
            subprocess.run(["systemctl", "--user", "restart", svc], shell=False, check=False)
        else:
            send_threaded_reply(config, message, f"Unknown restart target: {target}", tag="Error")


def relay_outbound_messages(config: dict, chat_db: ChatDB) -> None:
    """Pick up pending agent-to-user messages and send them as emails.

    On permanent SMTP errors, the message is marked failed so it won't be
    retried forever. On transient errors, it stays pending and we stop
    iterating to avoid hammering a broken connection.
    """
    pending = chat_db.get_pending_messages_for("user")
    for msg in pending:
        content_type = msg.get("content_type") or "text/plain"
        subj_base = f"[{msg['from_name']}] message"
        subject = subj_base if content_type == "application/json" else prepend_tag(
            subj_base, tag_for_message_type(msg.get("type") or ""),
        )
        thread_id = thread_id_for_message(chat_db, msg)
        try:
            email_msg_id = send_reply(
                smtp_host=config["smtp_host"], smtp_port=config["smtp_port"],
                username=config["username"], password=config["password"],
                to=recipient_for_message(chat_db, msg, config),
                subject=subject, body=msg["body"],
                in_reply_to=thread_id, references=thread_id,
                email_domain=config.get("email_domain", ""),
                content_type=content_type,
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
