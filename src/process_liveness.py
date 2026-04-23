"""Process liveness probing with zombie reaping.

Separated from chat_db.py so the DB layer stays pure storage and the process
management logic can be tested on its own. Import sites call ``is_alive(pid)``
and get a truthful answer: a zombie child we parented gets waitpid()'d
and reported as dead; a live process (ours or not) is reported as alive.

Also exposes PPID-chain helpers used by the Claude Code hook scripts to
distinguish "a hook running under my long-lived Claude session" from "a
different live Claude session owns this agent slot". The hook scripts
can't rely on their own PID — they're short-lived helpers — so ownership
is expressed as ancestry instead of identity.
"""
import os
from pathlib import PurePosixPath


_PPID_WALK_MAX_DEPTH = 64


def _get_ppid(pid: int) -> int | None:
    """Return the parent PID of ``pid`` by reading /proc, or None on
    any failure (vanished process, non-Linux, read error)."""
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("PPid:"):
                    return int(line.split()[1])
    except (OSError, ValueError):
        return None
    return None


def _read_cmdline(pid: int) -> str:
    """Return /proc/<pid>/cmdline as a single string (NULs preserved as
    whitespace), or empty string on failure."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def is_ancestor_or_self(target_pid: int) -> bool:
    """True iff ``target_pid`` is the current process or one of its
    PPID-chain ancestors (up to init). Used by hook scripts to decide
    "does the registered agent PID belong to the Claude session that
    launched me?" — sibling sessions won't match."""
    if target_pid <= 0:
        return False
    current = os.getpid()
    for _ in range(_PPID_WALK_MAX_DEPTH):
        if current == target_pid:
            return True
        parent = _get_ppid(current)
        if parent is None or parent <= 1:
            return False
        current = parent
    return False


def _argv0_basename(pid: int) -> str:
    """Return the basename of argv[0] for ``pid``, or empty string on
    failure. /proc/<pid>/cmdline is NUL-separated; the first token is
    argv[0] (may be a bare name or an absolute path)."""
    raw = _read_cmdline(pid)
    if not raw:
        return ""
    argv0 = raw.split("\x00", 1)[0]
    return PurePosixPath(argv0).name


def find_ancestor_pid_matching(argv0_basename: str) -> int | None:
    """Walk up the PPID chain from our parent and return the first
    ancestor whose argv[0] basename equals ``argv0_basename``. Returns
    None if no ancestor matches or /proc is unreadable.

    Matching is basename-equality, not substring search: a shell wrapper
    whose script path contains the marker (e.g. the SessionStart hook at
    .../claude-email/scripts/chat-session-start-hook.sh) would otherwise
    false-match before the walker reaches the long-lived CLI, and its
    short-lived PID would end up in the agents table."""
    pid = _get_ppid(os.getpid())
    for _ in range(_PPID_WALK_MAX_DEPTH):
        if pid is None or pid <= 1:
            return None
        if _argv0_basename(pid) == argv0_basename:
            return pid
        pid = _get_ppid(pid)
    return None


def is_alive(pid: int) -> bool:
    """Return True iff the process ``pid`` is still running.

    Reaps zombie children as a side effect — ``os.kill(pid, 0)`` succeeds
    for zombies because the PID is still in the kernel's process table, so
    a naive liveness check would leak zombies forever. We try
    ``os.waitpid(pid, WNOHANG)`` first (reap if we parented it); fall back
    to ``os.kill(pid, 0)`` for PIDs that were never our children.

    Non-positive PIDs are rejected without touching waitpid/kill: pid 0
    and negatives have special POSIX semantics (they address process
    groups, not individual PIDs), so a corrupted DB entry must never
    reach those calls.
    """
    if pid <= 0:
        return False
    try:
        waited, _ = os.waitpid(pid, os.WNOHANG)
        if waited == pid:
            return False
    except ChildProcessError:
        pass  # Not our child — liveness probe below tells us.
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True
