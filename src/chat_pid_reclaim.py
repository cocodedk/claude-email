"""PID reclaim for the drain hook.

Extracted from ``scripts/chat-drain-inbox.py`` to keep the hook script
under the project's 200-line limit. Import it back from the script as
orchestration glue — the helper itself is self-contained.
"""
from __future__ import annotations

import os
import sys

from src.chat_db import ChatDB
from src.chat_errors import AgentNameTaken, AgentProjectTaken
from src.process_liveness import find_ancestor_pid_matching

_CLAUDE_CMDLINE_MARKER = os.environ.get("CLAUDE_PROCESS_MARKER", "claude")


def reclaim_pid_best_effort(db: ChatDB, caller: str, cwd: str) -> None:
    """Ensure the agent row stores the current Claude session pid.

    Heals rows left stale by two earlier paths:
      - SessionStart's hook didn't register this session (older configs
        used a ``startup|resume`` matcher that skipped ``compact`` /
        ``continue`` sources; newer configs use an empty matcher but the
        belt-and-braces repair is still worth running on every drain).
      - chat-register-self.py fell back to ``os.getpid()`` because the
        PPID walker found no ``claude`` ancestor (hook-helper spawn
        layout varies), stamping a short-lived helper pid.

    No-op when: walker finds no claude ancestor; row does not exist
    (registration is chat-register-self.py's job); stored pid already
    matches. Swallows ``AgentNameTaken`` / ``AgentProjectTaken`` so a
    live sibling session keeps its slot — the downstream sibling-
    ownership gate (``is_ancestor_or_self``) distinguishes sibling from
    self/ancestor and is the authoritative skip decision; returning
    early here would throw away that intelligence and wrongly skip
    drain when the conflict is with our own ancestor pid. Never raises
    — a broken bus must not block drain.
    """
    try:
        claude_pid = find_ancestor_pid_matching(_CLAUDE_CMDLINE_MARKER)
        if claude_pid is None:
            return
        agent = db.get_agent(caller)
        if agent is None:
            return
        if agent.get("pid") == claude_pid:
            return
        try:
            db.register_agent(caller, cwd, pid=claude_pid)
        except (AgentNameTaken, AgentProjectTaken):
            return
    except Exception as exc:  # noqa: BLE001
        print(f"chat-drain-inbox: pid reclaim failed: {exc}", file=sys.stderr)
