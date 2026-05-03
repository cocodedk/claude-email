"""Inbound JSON-envelope dispatch: parse → act → reply in JSON.

Per-kind handlers live in ``src/json_kinds.py`` so this file stays
focused on entry, auth, dispatch routing, and the SMTP reply send.
"""
import logging

from src.chat_db import ChatDB
from src.error_codes import make_error
from src.json_envelope import Envelope, EnvelopeError, build_envelope, parse_envelope
from src.json_kinds import (
    handle_cancel, handle_command, handle_list_projects, handle_status,
)
from src.mailer import send_reply
from src.task_queue import TaskQueue
from src.worker_manager import WorkerManager

logger = logging.getLogger(__name__)


def handle_json_email(
    message, config: dict, chat_db: ChatDB,
    task_queue: TaskQueue, worker_manager: WorkerManager,
) -> bool:
    """Run the JSON path end-to-end. Returns True iff a JSON reply was sent."""
    try:
        env = parse_envelope(message)
    except EnvelopeError as exc:
        logger.warning("JSON envelope parse failed: %s", exc.code)
        _send_json_reply(config, message, build_envelope(
            "error", body=exc.message,
            error=make_error(exc.code, exc.message),
            ask_id=exc.ask_id,
        ), chat_db=chat_db)
        return True

    universe = config.get("_universe")
    expected = (universe.shared_secret if universe else config.get("shared_secret", "")) or ""
    if expected and env.auth != expected:
        logger.warning(
            "JSON auth mismatch: meta.auth len=%d, expected len=%d",
            len(env.auth), len(expected),
        )
        _send_json_reply(config, message, build_envelope(
            "error", body="auth failed",
            error=make_error(
                "unauthorized", "meta.auth does not match",
                hint="Open Settings and re-enter the shared secret.",
            ),
            ask_id=env.ask_id,
        ), chat_db=chat_db)
        return True

    logger.info("JSON envelope accepted: kind=%s project=%s", env.kind, env.project)
    inbound_msg_id = message.get("Message-ID", "")
    inbound_subject = message.get("Subject", "")
    inbound_from = config.get("reply_to") or config.get("authorized_sender", "")
    reply = _dispatch(
        env, config, task_queue, worker_manager, chat_db,
        inbound_msg_id, inbound_subject, inbound_from,
    )
    _send_json_reply(config, message, reply, chat_db=chat_db)
    return True


def _dispatch(
    env: Envelope, config, task_queue, worker_manager, chat_db=None,
    inbound_msg_id: str = "", inbound_subject: str = "",
    inbound_from: str = "",
) -> str:
    universe = config.get("_universe")
    allowed_base = universe.allowed_base if universe else config.get("claude_cwd", "")
    if env.kind == "command":
        return handle_command(
            env, task_queue, worker_manager, allowed_base,
            inbound_msg_id, inbound_subject, inbound_from,
        )
    if env.kind == "status":
        return handle_status(env, task_queue, allowed_base)
    if env.kind == "cancel":
        return handle_cancel(env, task_queue, allowed_base)
    if env.kind == "list_projects":
        return handle_list_projects(env, task_queue, allowed_base, chat_db=chat_db)
    msg = f"kind {env.kind!r} comes online in a later phase"
    return build_envelope(
        "error", body=f"kind {env.kind!r} not yet implemented",
        error=make_error("not_implemented", msg),
        ask_id=env.ask_id,
    )


def _send_json_reply(
    config, original_message, body_json: str, chat_db: ChatDB | None = None,
) -> None:
    subject = original_message.get("Subject", "command")
    msg_id = original_message.get("Message-ID", "")
    try:
        sent_id = send_reply(
            smtp_host=config["smtp_host"], smtp_port=config["smtp_port"],
            username=config["username"], password=config["password"],
            to=config.get("reply_to") or config["authorized_sender"],
            subject=subject,
            body=body_json, in_reply_to=msg_id, references=msg_id,
            email_domain=config.get("email_domain", ""),
            content_type="application/json",
        )
    except Exception:
        logger.exception("JSON reply send failed")
        return
    if chat_db is not None and sent_id:
        chat_db.record_outbound_email(sent_id, kind="envelope_reply")
