"""Wake-turn subprocess builder and runner for the claude-chat wake watcher.

Kept deliberately pure: `build_wake_cmd` returns argv, `run_wake_turn`
invokes it via asyncio (no shell). Both are trivially mockable so the
watcher's control flow can be tested without ever launching `claude`.
"""
import asyncio
from asyncio.subprocess import DEVNULL, create_subprocess_exec as _launch_proc
from dataclasses import dataclass


def build_wake_cmd(
    claude_bin: str, session_id: str, is_resume: bool, prompt: str,
) -> list[str]:
    flag = "--resume" if is_resume else "--session-id"
    return [claude_bin, "--print", flag, session_id, prompt]


@dataclass
class WakeTurnResult:
    exit_code: int
    timed_out: bool
    error: str | None = None


async def run_wake_turn(
    cmd: list[str], cwd: str, timeout: float,
) -> WakeTurnResult:
    """Run a wake subprocess. stdout/stderr discarded; only exit code matters.

    stdin is closed (DEVNULL) so the child cannot accidentally block on
    inherited console input. On timeout we kill the process and wait at
    most 5s for it to reap, to avoid hanging on an uncooperative child.
    """
    try:
        proc = await _launch_proc(
            *cmd, cwd=cwd,
            stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL,
        )
    except (FileNotFoundError, PermissionError) as exc:
        return WakeTurnResult(exit_code=-1, timed_out=False, error=str(exc))
    try:
        exit_code = await asyncio.wait_for(proc.wait(), timeout=timeout)
        return WakeTurnResult(exit_code=exit_code, timed_out=False)
    except asyncio.TimeoutError:
        proc.kill()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            pass  # child refuses to reap; release the coroutine anyway
        return WakeTurnResult(exit_code=-1, timed_out=True)
