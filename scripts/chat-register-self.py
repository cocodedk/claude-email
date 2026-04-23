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

from src.chat_db import AgentNameTaken, AgentProjectTaken, ChatDB  # noqa: E402
from src.process_liveness import (  # noqa: E402
    find_ancestor_pid_matching, is_ancestor_or_self,
)


_CLAUDE_CMDLINE_MARKER = os.environ.get("CLAUDE_PROCESS_MARKER", "claude")


def _durable_session_pid() -> int:
    """Return a PID that will still be alive on later hook invocations.

    Hook helpers are short-lived — os.getpid() here dies when this script
    exits. To make ownership checks meaningful across subsequent hooks we
    store the long-lived Claude session PID instead, found by walking up
    the PPID chain for a process whose cmdline contains ``claude``. Falls
    back to os.getpid() when no Claude ancestor is visible (e.g. ad-hoc
    CLI runs, CI, tests) so existing standalone paths still work.

    The marker is "claude" (not "bin/claude") because /proc/<pid>/cmdline
    for an interactive Claude CLI is just ``claude --flags…`` — the full
    path isn't recorded in argv[0] when the binary is invoked via PATH.
    Using the looser match fixes the pid=NULL / pid-dead rows that
    appeared in the agents table and made live sessions invisible on
    the dashboard."""
    return find_ancestor_pid_matching(_CLAUDE_CMDLINE_MARKER) or os.getpid()


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


def _read_hook_payload() -> dict:
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


def main() -> int:
    payload = _read_hook_payload()
    if payload.get("agent_id"):
        # Subagent: Claude Code only sets agent_id for subagent hook
        # invocations. The master session already registered; don't
        # register again under the same cwd-derived name.
        return 0
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
    session_pid = _durable_session_pid()
    try:
        db = ChatDB(str(db_path))
    except Exception as exc:  # noqa: BLE001
        print(f"chat-register-self: cannot open DB: {exc}", file=sys.stderr)
        return 1
    if _master_already_owns(db, name, cwd):
        # Another live claude session or parent agent already holds this
        # slot — subagents and sibling sessions must stay silent.
        return 0
    try:
        db.register_agent(name, cwd, pid=session_pid)
    except (AgentNameTaken, AgentProjectTaken):
        # Race: someone else registered between our pre-check and insert.
        # Quietly concede.
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"chat-register-self: registration failed: {exc}", file=sys.stderr)
        return 1
    return 0


def _master_already_owns(db: ChatDB, name: str, cwd: str) -> bool:
    """True if another live process (not in our PPID chain) already owns
    this agent name or project path. Uses PPID ancestry instead of PID
    equality because hook helpers are short-lived — the hook's os.getpid()
    is never what's stored in the DB for a properly-registered master.

    Delegates the DB scan to ChatDB.find_live_owner so all ownership
    logic lives in one place and the script avoids reaching into
    db._conn directly.
    """
    owner = db.find_live_owner(name, cwd)
    if owner is None:
        return False
    return not is_ancestor_or_self(owner["pid"])


if __name__ == "__main__":
    sys.exit(main())
