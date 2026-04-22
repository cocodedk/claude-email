"""Startup reconciliation of live Claude CLIs → agents table.

Bridges the gap between "claude-chat was restarted" and "every
SessionStart hook eventually fires again". When the bus bounces, live
Claude sessions keep running but their agent rows no longer reflect
reality — the stored PID may be stale or absent, so the dashboard's
is_alive filter hides them. We fix that on boot by scanning /proc for
`claude` CLIs, deriving the agent name from each process's cwd, and
upserting the row with a currently-live PID.

Purely local (reads /proc + writes DB). Non-Linux hosts naturally
no-op because /proc isn't populated.
"""
import logging
import os
from pathlib import PurePosixPath

logger = logging.getLogger(__name__)

_DEFAULT_MARKER = "claude"


def _iter_claude_pids(marker: str = _DEFAULT_MARKER) -> list[int]:
    """Return PIDs whose cmdline argv[0] basename matches ``marker``."""
    pids: list[int] = []
    try:
        entries = os.listdir("/proc")
    except (FileNotFoundError, PermissionError):
        return []
    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                data = f.read()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if not data:
            continue
        argv0 = data.split(b"\x00", 1)[0].decode("latin-1", "replace")
        if PurePosixPath(argv0).name == marker:
            pids.append(pid)
    return pids


def _cwd_of(pid: int) -> str | None:
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        return None


def reconcile_live_agents(db, *, marker: str = _DEFAULT_MARKER) -> list[str]:
    """Scan /proc for running Claude CLIs and upsert their agent rows.

    Returns the list of agent names that were refreshed. Failed upserts
    are logged and skipped so one hostile row never blocks the others.
    """
    touched: list[str] = []
    for pid in _iter_claude_pids(marker):
        cwd = _cwd_of(pid)
        if not cwd:
            continue
        name = "agent-" + PurePosixPath(cwd).name
        try:
            db.register_agent(name, cwd, pid=pid)
        except Exception:  # noqa: BLE001
            logger.exception(
                "reconcile: failed to upsert %s (pid %d)", name, pid,
            )
            continue
        touched.append(name)
    if touched:
        logger.info(
            "reconcile: refreshed %d live claude session(s): %s",
            len(touched), ", ".join(touched),
        )
    return touched
