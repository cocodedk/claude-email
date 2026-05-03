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

from src.agent_name import validated_agent_name
from src.chat_errors import AgentNameTaken, AgentProjectTaken

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


def _read_agent_name_from_environ(pid: int) -> str | None:
    """Return the value of CLAUDE_AGENT_NAME from /proc/<pid>/environ, if any.

    /proc/<pid>/environ is a null-separated bytes blob of KEY=VALUE pairs.
    Returns None when the file is unreadable, the variable is absent, or
    the value can't be decoded as UTF-8. Validation is the caller's job —
    this helper just extracts the raw string."""
    try:
        with open(f"/proc/{pid}/environ", "rb") as f:
            data = f.read()
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        return None
    for entry in data.split(b"\x00"):
        if entry.startswith(b"CLAUDE_AGENT_NAME="):
            try:
                return entry.split(b"=", 1)[1].decode("utf-8")
            except UnicodeDecodeError:
                return None
    return None


def _fallback_name(cwd: str, pid: int) -> str:
    """Disambiguator for basename collisions across projects.

    `/home/u/work/app` and `/home/u/backup/app` both derive ``agent-app``
    — the second registration would raise AgentNameTaken and the hostile
    project would stay invisible. Fall back to ``agent-<parent>-<basename>``
    so both distinct projects can share the radar. pid is appended only
    if the parent-qualified name still collides (e.g. two checkouts of
    the same repo under the same parent directory)."""
    path = PurePosixPath(cwd)
    parent = path.parent.name or "root"
    return f"agent-{parent}-{path.name}"


def reconcile_live_agents(db, *, marker: str = _DEFAULT_MARKER) -> list[str]:
    """Scan /proc for running Claude CLIs and upsert their agent rows.

    Returns the list of agent names that were refreshed. On basename
    collision (``AgentNameTaken`` / ``AgentProjectTaken`` from another
    project sharing the same cwd tail), retry once with a parent-qualified
    name so both live sessions end up visible. Any other failure is
    logged and skipped so one hostile row can't block the others.
    """
    touched: list[str] = []
    for pid in _iter_claude_pids(marker):
        cwd = _cwd_of(pid)
        if not cwd:
            continue
        fallback = "agent-" + PurePosixPath(cwd).name
        env_name = _read_agent_name_from_environ(pid)
        name = validated_agent_name(env_name, fallback)
        try:
            db.register_agent(name, cwd, pid=pid)
        except (AgentNameTaken, AgentProjectTaken):
            fallback = _fallback_name(cwd, pid)
            logger.info(
                "reconcile: %s slot held elsewhere — retrying as %s",
                name, fallback,
            )
            try:
                db.register_agent(fallback, cwd, pid=pid)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "reconcile: fallback upsert failed for %s (pid %d)",
                    fallback, pid,
                )
                continue
            name = fallback
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
