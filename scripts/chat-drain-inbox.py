#!/usr/bin/env python3
"""Drain pending chat messages for this agent and emit them as hook context.

Runs from a Claude Code SessionStart, UserPromptSubmit, or Stop hook. Reads
from the shared SQLite bus, marks each message delivered (same consume-with-ack
semantics as the chat_check_messages MCP tool), and prints a hook JSON payload
so the model sees them on its next turn — instead of depending on the model
to call chat_check_messages itself.

Shape depends on the triggering event:
  - SessionStart / UserPromptSubmit → hookSpecificOutput + additionalContext
  - Stop → {"decision": "block", "reason": ...} so the end-of-turn stop is
    cancelled and the drained messages become the next thing Claude sees.

Stop is the "push-like" path: it fires when Claude finishes a response, so
peer messages that arrived mid-response get surfaced before the session idles.
stop_hook_active is intentionally ignored — mark_message_delivered is the
real loop guard (same msg can't be re-emitted), so sustained peer chatter is
allowed to keep the agent conversant.

Emits no stdout when the inbox is empty — quiet turns stay quiet.
"""
import json
import os
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from src.agent_name import ENV_VAR_NAME, validated_agent_name  # noqa: E402
from src.chat_db import ChatDB  # noqa: E402
from src.chat_pid_reclaim import reclaim_pid_best_effort  # noqa: E402
from src.process_liveness import is_alive, is_ancestor_or_self  # noqa: E402


def _resolved_db_path() -> Path:
    raw = os.environ.get("CHAT_DB_PATH", "")
    if not raw:
        raise RuntimeError(
            "CHAT_DB_PATH not set — expected it in .env (e.g. claude-chat.db).",
        )
    p = Path(raw)
    return p if p.is_absolute() else ROOT / p


def _caller_name() -> str:
    """Return the bus identity to drain mail for.

    Honors CLAUDE_AGENT_NAME (set by the spawner / SessionStart-time
    shell export) so a session that registered under a non-default name
    drains its OWN inbox, not the cwd-default name's inbox. Falls back
    to ``agent-<basename(cwd)>`` when the env var is unset or invalid —
    matching what the SessionStart hook would have registered."""
    fallback = "agent-" + PurePosixPath(os.getcwd()).name
    return validated_agent_name(os.environ.get(ENV_VAR_NAME), fallback)


def _read_hook_payload() -> dict:
    """Parse the JSON payload that Claude Code pipes on stdin to a hook.

    Returns {} when stdin is a tty, empty, unavailable, or malformed.
    """
    try:
        if sys.stdin.isatty():
            return {}
        data = sys.stdin.read()
    except (OSError, ValueError):
        return {}
    if not data.strip():
        return {}
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return {}


def _read_hook_event() -> str:
    """Back-compat wrapper used by existing tests."""
    return _read_hook_payload().get("hook_event_name") or "UserPromptSubmit"


def _format_context(caller: str, msgs: list[dict]) -> str:
    lines = [
        "INBOX (already consumed from the bus — do NOT call "
        "mcp__claude-chat__chat_check_messages for these):",
    ]
    for m in msgs:
        lines.append(
            f"  [msg #{m['id']}] from={m['from_name']} "
            f"at {m['created_at']}: {m['body']}"
        )
    lines.append("")
    lines.append(
        f'Respond via mcp__claude-chat__chat_notify(_caller="{caller}", '
        f'message="...") or answer inline if the sender is "user" and the '
        f"session is already in that conversation.",
    )
    return "\n".join(lines)


def main() -> int:
    payload = _read_hook_payload()
    if payload.get("agent_id"):
        # Claude Code marks subagent hook invocations with agent_id; the
        # master session owns the bus slot — sub-agents must not drain.
        return 0
    event = payload.get("hook_event_name") or "UserPromptSubmit"
    try:
        db_path = _resolved_db_path()
    except RuntimeError as exc:
        print(f"chat-drain-inbox: {exc}", file=sys.stderr)
        return 0  # fail open — never block a session
    if not db_path.exists():
        print(
            f"chat-drain-inbox: DB {db_path} does not exist — is claude-chat running?",
            file=sys.stderr,
        )
        return 0
    try:
        db = ChatDB(str(db_path))
    except Exception as exc:  # noqa: BLE001
        print(f"chat-drain-inbox: cannot open DB: {exc}", file=sys.stderr)
        return 0

    caller = _caller_name()
    reclaim_pid_best_effort(db, caller, os.getcwd())
    agent = db.get_agent(caller)
    if (
        agent is not None
        and agent["pid"] is not None
        and not is_ancestor_or_self(agent["pid"])
        and is_alive(agent["pid"])
    ):
        # A different live process owns this agent name and is NOT in our
        # PPID chain — so it's a sibling Claude session, not the one that
        # launched this hook. Silent skip so sibling sessions don't steal
        # each other's messages. (Matching os.getpid() directly was wrong:
        # hook scripts are short-lived helpers, never the stored PID.)
        return 0
    try:
        msgs = db.claim_pending_messages_for(caller)
    except Exception as exc:  # noqa: BLE001
        print(f"chat-drain-inbox: query failed: {exc}", file=sys.stderr)
        return 0
    if not msgs:
        return 0  # quiet turn

    context = _format_context(caller, msgs)
    if event == "Stop":
        payload = {"decision": "block", "reason": context}
        flow_type = "hook_drain_stop"
    else:
        payload = {
            "hookSpecificOutput": {
                "hookEventName": event,
                "additionalContext": context,
            },
        }
        flow_type = "hook_drain_session"
    try:
        db._log_event(caller, flow_type, f"drained={len(msgs)} event={event}")
    except Exception as exc:  # noqa: BLE001
        # Never block the session on telemetry, but leave a diagnostic trail
        # so a broken events insert doesn't turn the flow panel silent.
        print(
            f"chat-drain-inbox: flow event log failed ({flow_type}): {exc}",
            file=sys.stderr,
        )
    sys.stdout.write(json.dumps(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
