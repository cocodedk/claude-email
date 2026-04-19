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
        _send_json_reply(config, message, build_envelope(
            "error", body=exc.message,
            error={"code": exc.code, "message": exc.message},
        ))
        return True

    # Re-auth per-envelope: the auth token in meta.auth must match the
    # scoped universe's shared_secret. Envelope-inside-body auth keeps
    # plain-text auth_prefix paths undisturbed.
    universe = config.get("_universe")
    expected = (universe.shared_secret if universe else config.get("shared_secret", "")) or ""
    if expected and env.auth != expected:
        _send_json_reply(config, message, build_envelope(
            "error", body="auth failed",
            error={"code": "unauthorized", "message": "meta.auth does not match"},
        ))
        return True

    reply = _dispatch(env, config, chat_db, task_queue, worker_manager)
    _send_json_reply(config, message, reply)
    return True


def _dispatch(env: Envelope, config, chat_db, task_queue, worker_manager) -> str:
    universe = config.get("_universe")
    allowed_base = universe.allowed_base if universe else config.get("claude_cwd", "")
    if env.kind == "command":
        return _handle_command(env, task_queue, worker_manager, allowed_base)
    # reply / status / cancel / retry / commit / reset / confirm_reset not wired yet
    return build_envelope(
        "error", body=f"kind {env.kind!r} not yet implemented in Phase 8a",
        error={"code": "invalid_state", "message": f"kind {env.kind!r} comes online in a later phase"},
    )


def _handle_command(env, task_queue, worker_manager, allowed_base) -> str:
    if not env.project or not env.body:
        return build_envelope(
            "error", body="command requires project + body",
            error={"code": "bad_envelope", "message": "missing project or body"},
        )
    if enqueue_task_tool is None:  # pragma: no cover
        return build_envelope(
            "error", body="server not fully initialized",
            error={"code": "internal", "message": "enqueue_task_tool unavailable"},
        )
    result = enqueue_task_tool(
        task_queue, worker_manager,
        project=env.project, body=env.body,
        priority=env.priority or 0, plan_first=env.plan_first,
        allowed_base=allowed_base,
    )
    if "error" in result:
        code = "project_not_found" if "does not exist" in result["error"] else "invalid_state"
        return build_envelope(
            "error", body=result["error"],
            error={"code": code, "message": result["error"]},
        )
    return build_envelope(
        "ack", body=f"Queued as task #{result['task_id']}.",
        task_id=result["task_id"],
        data={
            "status": "queued",
            "branch": result["planned_branch"],
            "worker_pid": result["worker_pid"],
            "plan_first": result.get("plan_first", False),
        },
    )


def _send_json_reply(config, original_message, body_json: str) -> None:
    subject = original_message.get("Subject", "command")
    msg_id = original_message.get("Message-ID", "")
    try:
        send_reply(
            smtp_host=config["smtp_host"], smtp_port=config["smtp_port"],
            username=config["username"], password=config["password"],
            to=config["authorized_sender"], subject=subject,
            body=body_json, in_reply_to=msg_id, references=msg_id,
            email_domain=config.get("email_domain", ""),
            content_type="application/json",
        )
    except Exception:
        logger.exception("JSON reply send failed")
