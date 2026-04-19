#!/usr/bin/env python3
"""Server-side pre-register for a SessionStart hook.

Derives agent name from the current working directory, resolves the chat DB
path the same way the chat server does (repo-relative via .env), and writes
a registration row directly through ChatDB.

Called by scripts/chat-session-start-hook.sh before the model is given its
first turn — gives us deterministic registration even if the model chooses
not to call chat_register itself.

Silent on success; errors go to stderr and exit with non-zero. Callers
(the shell hook) are expected to `|| true` so a broken bus never blocks
a session from starting.
"""
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
    """Return the chat DB path, resolved against the claude-email repo root
    when CHAT_DB_PATH is relative (matching chat_server.py's behaviour)."""
    raw = os.environ.get("CHAT_DB_PATH", "")
    if not raw:
        raise RuntimeError(
            "CHAT_DB_PATH not set — expected it in .env "
            "(e.g. claude-chat.db).",
        )
    path = Path(raw)
    return path if path.is_absolute() else ROOT / path


def main() -> int:
    cwd = os.getcwd()
    name = "agent-" + PurePosixPath(cwd).name
    try:
        db_path = _resolved_db_path()
    except RuntimeError as exc:
        print(f"chat-register-self: {exc}", file=sys.stderr)
        return 2
    if not db_path.exists():
        print(
            f"chat-register-self: DB {db_path} does not exist — is claude-chat running?",
            file=sys.stderr,
        )
        return 1
    try:
        db = ChatDB(str(db_path))
        db.register_agent(name, cwd)
    except Exception as exc:  # noqa: BLE001
        print(f"chat-register-self: registration failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
