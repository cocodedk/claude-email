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
