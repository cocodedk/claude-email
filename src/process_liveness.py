"""Process liveness probing with zombie reaping.

Separated from chat_db.py so the DB layer stays pure storage and the process
management logic can be tested on its own. Import sites call ``is_alive(pid)``
and get a truthful answer: a zombie child we parented gets waitpid()'d
and reported as dead; a live process (ours or not) is reported as alive.
"""
import os


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
