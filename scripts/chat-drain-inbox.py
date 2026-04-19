#!/usr/bin/env python3
"""Drain pending chat messages for this agent and emit them as hook context.

Runs from a Claude Code SessionStart or UserPromptSubmit hook. Reads from the
shared SQLite bus, marks each message delivered (same consume-with-ack
semantics as the chat_check_messages MCP tool), and prints a hook JSON payload
whose additionalContext lists the drained messages so the model sees them on
its next turn — instead of depending on the model to call chat_check_messages
itself.

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

from src.chat_db import ChatDB  # noqa: E402


def _resolved_db_path() -> Path:
    raw = os.environ.get("CHAT_DB_PATH", "")
    if not raw:
        raise RuntimeError(
            "CHAT_DB_PATH not set — expected it in .env (e.g. claude-chat.db).",
        )
    p = Path(raw)
    return p if p.is_absolute() else ROOT / p


def _caller_name() -> str:
    return "agent-" + PurePosixPath(os.getcwd()).name


def _read_hook_event() -> str:
    """Hook runtime passes JSON on stdin with hook_event_name. Fall back to
    UserPromptSubmit when nothing is piped (standalone / tests)."""
    if sys.stdin.isatty():
        return "UserPromptSubmit"
    data = sys.stdin.read()
    if not data.strip():
        return "UserPromptSubmit"
    try:
        payload = json.loads(data)
        return payload.get("hook_event_name") or "UserPromptSubmit"
    except json.JSONDecodeError:
        return "UserPromptSubmit"


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
    event = _read_hook_event()
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
    try:
        msgs = db.get_pending_messages_for(caller)
    except Exception as exc:  # noqa: BLE001
        print(f"chat-drain-inbox: query failed: {exc}", file=sys.stderr)
        return 0
    if not msgs:
        return 0  # quiet turn

    for m in msgs:
        db.mark_message_delivered(m["id"])

    payload = {
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": _format_context(caller, msgs),
        },
    }
    sys.stdout.write(json.dumps(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
