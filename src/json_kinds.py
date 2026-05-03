"""Per-``kind`` handlers for the JSON envelope dispatcher.

Split from ``src/json_handler.py`` so each file stays under the 200-line
cap. Each ``_handle_<kind>`` returns the JSON-serialized envelope text
that ``_send_json_reply`` will SMTP back to the client.
"""
from src.error_codes import make_error
from src.json_envelope import (
    ROUTED_VIA_AGENT, ROUTED_VIA_WORKER, Envelope, build_envelope,
)

try:
    from chat.project_tools import (  # noqa: E402
        cancel_task_tool, enqueue_task_tool, list_projects_tool,
        queue_status_tool, resolve_project,
    )
except ImportError:  # pragma: no cover
    cancel_task_tool = enqueue_task_tool = queue_status_tool = None
    list_projects_tool = resolve_project = None


def _bad_envelope(env: Envelope, body: str, message: str) -> str:
    return build_envelope(
        "error", body=body,
        error=make_error("bad_envelope", message),
        ask_id=env.ask_id,
    )


def _tool_error(env: Envelope, result: dict) -> str:
    code = result.get("error_code", "invalid_state")
    return build_envelope(
        "error", body=result["error"],
        error=make_error(code, result["error"]),
        ask_id=env.ask_id,
    )


def _server_uninitialized(env: Envelope, missing: str) -> str:  # pragma: no cover
    return build_envelope(
        "error", body="server not fully initialized",
        error=make_error("internal", f"{missing} unavailable"),
        ask_id=env.ask_id,
    )


def _ack(env: Envelope, body: str, data: dict, routed_via: str | None = None) -> str:
    return build_envelope(
        "ack", body=body, ask_id=env.ask_id, data=data, routed_via=routed_via,
    )


def handle_status(env: Envelope, task_queue, allowed_base: str) -> str:
    if not env.project:
        return _bad_envelope(env, "status requires project", "missing project")
    if queue_status_tool is None:  # pragma: no cover — chat package import broken
        return _server_uninitialized(env, "queue_status_tool")
    result = queue_status_tool(task_queue, project=env.project, allowed_base=allowed_base)
    if "error" in result:
        return _tool_error(env, result)
    return _ack(env, f"Status for {env.project}", {
        "running": result.get("running"),
        "pending": result.get("pending", []),
    })


def handle_list_projects(
    env: Envelope, task_queue, allowed_base: str, chat_db=None,
) -> str:
    if list_projects_tool is None:  # pragma: no cover — chat package import broken
        return _server_uninitialized(env, "list_projects_tool")
    result = list_projects_tool(
        task_queue, allowed_base=allowed_base, chat_db=chat_db,
    )
    return _ack(env, f"{len(result['projects'])} project(s)", result)


def handle_cancel(env: Envelope, task_queue, allowed_base: str) -> str:
    if not env.project:
        return _bad_envelope(env, "cancel requires project", "missing project")
    if cancel_task_tool is None:  # pragma: no cover — chat package import broken
        return _server_uninitialized(env, "cancel_task_tool")
    result = cancel_task_tool(
        task_queue, project=env.project, allowed_base=allowed_base,
        drain_queue=env.drain_queue,
    )
    if "error" in result:
        return _tool_error(env, result)
    return _ack(env, f"Cancel: {result.get('status', 'unknown')}", result)


def _agent_message_body(inbound_from: str, inbound_subject: str, body: str, task_id: int) -> str:
    """Format the user→agent bus message so the recipient agent has
    sender + subject context AND knows to thread its reply via task_id."""
    sender = inbound_from or "the user"
    subject = inbound_subject or "(no subject)"
    return (
        f"[email from {sender}] {subject}\n\n"
        f"{body}\n\n"
        f"REQUIRED: reply via chat_message_agent(to_agent=\"user\", "
        f"task_id={task_id}, message=...) — SMTP routing back to the "
        f"original sender depends on the task_id."
    )


def _route_to_live_agent(
    env: Envelope, task_queue, chat_db, allowed_base: str,
    inbound_msg_id: str, inbound_subject: str, inbound_from: str,
) -> str | None:
    """Try the live-agent path. Returns an ack envelope on success, or
    ``None`` to signal "fall through to the worker spawn path"."""
    if chat_db is None:
        return None
    try:
        resolved = resolve_project(env.project, allowed_base)
    except ValueError:
        return None
    agent = chat_db.find_live_agent_for_project(resolved)
    if agent is None:
        return None
    virtual_id = task_queue.enqueue_routed(
        resolved, env.body,
        origin_content_type="application/json",
        origin_message_id=inbound_msg_id,
        origin_subject=inbound_subject,
        origin_from=inbound_from,
    )
    chat_db.insert_message(
        "user", agent["name"],
        _agent_message_body(inbound_from, inbound_subject, env.body, virtual_id),
        "ask", task_id=virtual_id,
    )
    return _ack(
        env, f"Routed to {agent['name']} (task #{virtual_id})",
        {"status": "routed", "agent": agent["name"], "task_id": virtual_id},
        routed_via=ROUTED_VIA_AGENT,
    )


def handle_command(
    env: Envelope, task_queue, worker_manager, allowed_base: str,
    inbound_msg_id: str = "", inbound_subject: str = "",
    inbound_from: str = "", chat_db=None,
) -> str:
    if not env.project or not env.body:
        return _bad_envelope(
            env, "command requires project + body",
            "missing project or body",
        )
    if enqueue_task_tool is None:  # pragma: no cover — chat package import broken
        return _server_uninitialized(env, "enqueue_task_tool")
    if env.prefer_live_agent:
        agent_ack = _route_to_live_agent(
            env, task_queue, chat_db, allowed_base,
            inbound_msg_id, inbound_subject, inbound_from,
        )
        if agent_ack is not None:
            return agent_ack
    result = enqueue_task_tool(
        task_queue, worker_manager,
        project=env.project, body=env.body,
        priority=env.priority or 0, plan_first=env.plan_first,
        allowed_base=allowed_base,
        origin_content_type="application/json",
        origin_message_id=inbound_msg_id,
        origin_subject=inbound_subject,
        origin_from=inbound_from,
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
        ask_id=env.ask_id, routed_via=ROUTED_VIA_WORKER,
        data={
            "status": "queued",
            "branch": result["planned_branch"],
            "worker_pid": result["worker_pid"],
            "plan_first": result.get("plan_first", False),
        },
    )
