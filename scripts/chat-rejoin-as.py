#!/usr/bin/env python3
"""Claim a non-default agent name on the claude-chat bus.

Usage: chat-rejoin-as.py <agent-name>

Walks the PPID chain to find the live Claude process (mirrors
chat-register-self.py's _durable_session_pid logic), validates the name
against src.agent_name's regex, and writes the agents row directly with
(name, project_path=cwd, pid=<live>). Backs the /chat-rejoin-as slash
command — the in-session escape hatch for sessions that started without
CLAUDE_AGENT_NAME exported and now need to claim a non-default identity.

On success: exit 0 and print the new identity. On AgentNameTaken (live
PID owns the slot): exit 1 with the owner pid in stderr. On invalid
name or missing DB: exit 2.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from src.agent_name import validated_agent_name  # noqa: E402
from src.chat_db import AgentNameTaken, ChatDB  # noqa: E402
from src.process_liveness import find_ancestor_pid_matching  # noqa: E402


_CLAUDE_CMDLINE_MARKER = os.environ.get("CLAUDE_PROCESS_MARKER", "claude")


def _durable_session_pid() -> int:
    """Walk PPID for the live Claude process; fall back to os.getpid()."""
    return find_ancestor_pid_matching(_CLAUDE_CMDLINE_MARKER) or os.getpid()


def _resolved_db_path() -> Path:
    raw = os.environ.get("CHAT_DB_PATH", "")
    if not raw:
        raise RuntimeError(
            "CHAT_DB_PATH not set — expected it in .env "
            "(e.g. claude-chat.db).",
        )
    path = Path(raw)
    return path if path.is_absolute() else ROOT / path


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"Usage: {Path(argv[0]).name} <agent-name>", file=sys.stderr)
        return 2
    raw = argv[1]
    name = validated_agent_name(raw, "")
    if name != raw:
        print(
            f"chat-rejoin-as: invalid agent name {raw!r} — "
            "must match ^agent-[a-z0-9][a-z0-9_-]{0,57}$",
            file=sys.stderr,
        )
        return 2

    try:
        db_path = _resolved_db_path()
    except RuntimeError as exc:
        print(f"chat-rejoin-as: {exc}", file=sys.stderr)
        return 2
    if not db_path.exists():
        print(
            f"chat-rejoin-as: DB {db_path} does not exist — is "
            "claude-chat running?",
            file=sys.stderr,
        )
        return 1

    pid = _durable_session_pid()
    cwd = os.getcwd()
    try:
        db = ChatDB(str(db_path))
    except Exception as exc:  # noqa: BLE001
        print(f"chat-rejoin-as: cannot open DB: {exc}", file=sys.stderr)
        return 1
    try:
        db.register_agent(name, cwd, pid=pid)
    except AgentNameTaken as exc:
        print(
            f"chat-rejoin-as: name {name!r} already held by live pid "
            f"{exc.owner_pid}",
            file=sys.stderr,
        )
        return 1
    print(f"Registered as {name} (pid {pid}) in {cwd}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
