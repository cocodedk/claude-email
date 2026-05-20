#!/usr/bin/env python3
"""PreCompact heartbeat: log one hook_precompact flow event per compaction.

Without this hook the dashboard's flow panel goes silent across context
compaction in long-running agent sessions — the bus has no way to know
the session is still alive and just rotating its working memory. Logging
a single `hook_precompact` event with the trigger captured in the summary
lets the dashboard render a heartbeat through the gap.

Best-effort telemetry only. Any failure (missing config, broken DB, write
error, malformed stdin) results in exit code 0 with a stderr diagnostic;
the hook never blocks the session and never writes to stdout. Sub-agent
invocations and sibling-owned slots are silently skipped — see
chat-drain-inbox.py for the same discriminators.
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

from src.chat_db import ChatDB  # noqa: E402
from src.chat_pid_reclaim import reclaim_pid_best_effort  # noqa: E402
from src.hook_utils import caller_name as _caller_name  # noqa: E402
from src.hook_utils import read_hook_payload as _read_hook_payload  # noqa: E402
from src.hook_utils import resolved_db_path as _resolved_db_path  # noqa: E402
from src.process_liveness import is_alive, is_ancestor_or_self  # noqa: E402


def main() -> int:
    payload = _read_hook_payload()
    if payload.get("agent_id"):
        return 0  # subagent — master session owns the bus slot
    try:
        db_path = _resolved_db_path(ROOT)
    except RuntimeError as exc:
        print(f"chat-precompact-hook: {exc}", file=sys.stderr)
        return 0
    if not db_path.exists():
        print(
            f"chat-precompact-hook: DB {db_path} does not exist — "
            f"is claude-chat running?",
            file=sys.stderr,
        )
        return 0
    try:
        db = ChatDB(str(db_path))
    except Exception as exc:  # noqa: BLE001
        print(f"chat-precompact-hook: cannot open DB: {exc}", file=sys.stderr)
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
        # Sibling Claude session owns this name — don't double-log.
        return 0

    trigger = payload.get("trigger") or "unknown"
    try:
        db._log_event(caller, "hook_precompact", f"trigger={trigger}")
    except Exception as exc:  # noqa: BLE001
        print(
            f"chat-precompact-hook: flow event log failed: {exc}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
