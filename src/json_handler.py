"""Inbound JSON-envelope dispatch: parse → act → reply in JSON.

Separated from chat_handlers to keep the two wire formats cleanly
distinct. Only the `command` kind has a full handler today; the others
are wired to clear "not-yet-implemented" error envelopes so the app
sees a deterministic stable-code response and doesn't think its message
was dropped.
"""
import json as _json
import logging

from src.chat_db import ChatDB
from src.error_codes import make_error
from src.json_envelope import Envelope, EnvelopeError, build_envelope, parse_envelope
from src.mailer import send_reply
from src.task_queue import TaskQueue
from src.worker_manager import WorkerManager

try:
    from chat.project_tools import enqueue_task_tool  # noqa: E402
except ImportError:  # pragma: no cover
    enqueue_task_tool = None

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
    reply = _dispatch(
        env, config, chat_db, task_queue, worker_manager,
        inbound_msg_id, inbound_subject,
    )
    _send_json_reply(config, message, reply, chat_db=chat_db)
    return True


def _dispatch(
    env: Envelope, config, chat_db, task_queue, worker_manager,
    inbound_msg_id: str = "", inbound_subject: str = "",
) -> str:
    universe = config.get("_universe")
    allowed_base = universe.allowed_base if universe else config.get("claude_cwd", "")
    if env.kind == "command":
        return _handle_command(
            env, task_queue, worker_manager, allowed_base,
            inbound_msg_id, inbound_subject,
        )
    msg = f"kind {env.kind!r} comes online in a later phase"
    return build_envelope(
        "error", body=f"kind {env.kind!r} not yet implemented",
        error=make_error("not_implemented", msg),
        ask_id=env.ask_id,
    )


def _handle_command(
    env, task_queue, worker_manager, allowed_base,
    inbound_msg_id="", inbound_subject="",
) -> str:
    if not env.project or not env.body:
        return build_envelope(
            "error", body="command requires project + body",
            error=make_error("bad_envelope", "missing project or body"),
            ask_id=env.ask_id,
        )
    if enqueue_task_tool is None:  # pragma: no cover
        return build_envelope(
            "error", body="server not fully initialized",
            error=make_error("internal", "enqueue_task_tool unavailable"),
            ask_id=env.ask_id,
        )
    result = enqueue_task_tool(
        task_queue, worker_manager,
        project=env.project, body=env.body,
        priority=env.priority or 0, plan_first=env.plan_first,
        allowed_base=allowed_base,
        origin_content_type="application/json",
        origin_message_id=inbound_msg_id,
        origin_subject=inbound_subject,
    )
    if "error" in result:
        code = result.get("error_code", "invalid_state")
        hint = (
            "Check the project name in Settings."
            if code == "project_not_found" else None
        )
        return build_envelope(
            "error", body=result["error"],
            error=make_error(code, result["error"], hint=hint),
            ask_id=env.ask_id,
        )
    return build_envelope(
        "ack", body=f"Queued as task #{result['task_id']}.",
        task_id=result["task_id"],
        ask_id=env.ask_id,
        data={
            "status": "queued",
            "branch": result["planned_branch"],
            "worker_pid": result["worker_pid"],
            "plan_first": result.get("plan_first", False),
        },
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
            to=config["authorized_sender"], subject=subject,
            body=body_json, in_reply_to=msg_id, references=msg_id,
            email_domain=config.get("email_domain", ""),
            content_type="application/json",
        )
    except Exception:
        logger.exception("JSON reply send failed")
        return
    if chat_db is not None and sent_id:
        chat_db.record_outbound_email(sent_id, kind="envelope_reply")
