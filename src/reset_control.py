"""Destructive project reset — two-step with a time-limited confirm token.

TokenStore is process-local: chat-chat is single-process, so a token
issued on step 1 survives until step 2 within the same running instance.
A server restart invalidates all tokens — acceptable, the two-step flow
already requires an explicit user round-trip.

perform_reset is the step-2 action: cancels anything running for the
project, drains the pending queue, then runs `git reset --hard HEAD` and
`git clean -fd` in the project cwd.
"""
import logging
import secrets
import subprocess
import time
import uuid

from src.task_control import cancel_running_task
from src.task_queue import TaskQueue

logger = logging.getLogger(__name__)


class TokenStore:
    def __init__(self, ttl_seconds: float = 300.0):
        self._ttl = ttl_seconds
        self._tokens: dict[str, tuple[str, float]] = {}  # token → (project, expires_at)

    def issue(self, project_path: str) -> str:
        token = f"{uuid.uuid4().hex[:8]}-{secrets.token_hex(4)}"
        self._tokens[token] = (project_path, time.monotonic() + self._ttl)
        return token

    def consume(self, project_path: str, token: str) -> bool:
        entry = self._tokens.get(token)
        if entry is None:
            return False
        project, expires_at = entry
        if project != project_path or time.monotonic() >= expires_at:
            self._tokens.pop(token, None)
            return False
        self._tokens.pop(token, None)
        return True

    def purge(self) -> None:
        now = time.monotonic()
        for token in [t for t, (_, exp) in self._tokens.items() if now >= exp]:
            self._tokens.pop(token, None)


def _run_git(argv: list[str], cwd: str) -> tuple[int, str]:
    proc = subprocess.run(
        argv, cwd=cwd, shell=False, capture_output=True, text=True, check=False,
    )
    output = (proc.stderr or "") + (proc.stdout or "")
    return proc.returncode, output.strip()


def perform_reset(queue: TaskQueue, project_path: str) -> dict:
    cancel_result = cancel_running_task(queue, project_path, drain_queue=True)
    cancelled_id = cancel_result.get("task_id")
    drained = cancel_result.get("drained", 0)

    rc, output = _run_git(["git", "reset", "--hard", "HEAD"], project_path)
    if rc != 0:
        return {"status": "reset_failed", "error": output or f"git reset rc={rc}"}
    rc, output = _run_git(["git", "clean", "-fd"], project_path)
    if rc != 0:
        return {"status": "reset_failed", "error": output or f"git clean rc={rc}"}

    return {
        "status": "reset",
        "cancelled_task_id": cancelled_id,
        "drained": drained,
    }
