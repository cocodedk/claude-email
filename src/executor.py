"""Run a command via the ``claude`` CLI.

Email-body parsing lives in ``src/email_extract.py``; this module owns
only the subprocess glue.
"""
import logging
import os
import subprocess

logger = logging.getLogger(__name__)

MAX_OUTPUT_BYTES = 50_000


def execute_command(
    command: str,
    claude_bin: str = "claude",
    timeout: int = 300,
    max_output_bytes: int = MAX_OUTPUT_BYTES,
    cwd: str | None = None,
    yolo: bool = False,
    extra_env: dict[str, str] | None = None,
    model: str | None = None,
    effort: str | None = None,
    max_budget_usd: str | None = None,
    system_prompt: str | None = None,
    mcp_config: str | None = None,
) -> str:
    """Execute a command via the claude CLI and return the output.

    Uses shell=False to prevent command injection.
    Truncates output to max_output_bytes.
    When cwd is set, the claude CLI runs in that directory.
    When yolo is True, passes --dangerously-skip-permissions so the agent
    auto-approves tool calls (needed for non-interactive email-driven runs).
    extra_env is merged over os.environ for the subprocess.
    Returns an error message on timeout or failure.
    """
    argv = [claude_bin]
    if yolo:
        argv.append("--dangerously-skip-permissions")
    if model:
        argv += ["--model", model]
    if effort:
        argv += ["--effort", effort]
    if system_prompt:
        argv += ["--append-system-prompt", system_prompt]
    if mcp_config:
        argv += ["--mcp-config", mcp_config]
    argv += ["--print", command]
    if max_budget_usd:
        argv += ["--max-budget-usd", max_budget_usd]
    env = {**os.environ, **extra_env} if extra_env else None
    logger.info("Executing command via claude CLI (timeout=%ds, yolo=%s)", timeout, yolo)
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            cwd=cwd,
            env=env,
        )
        output = result.stdout
        if result.stderr:
            output += f"\n[stderr]:\n{result.stderr}"
        if len(output.encode()) > max_output_bytes:
            output = output.encode()[:max_output_bytes].decode(errors="replace")
            output += "\n[truncated]"
        return output
    except subprocess.TimeoutExpired:
        logger.error("Command timed out after %ds", timeout)
        return f"[Error: command timed out after {timeout} seconds]"
    except FileNotFoundError:
        logger.error("claude binary not found at %r", claude_bin)
        return f"[Error: claude binary not found at {claude_bin!r}]"
    except Exception as exc:  # noqa: BLE001
        logger.error("Unexpected error executing command: %s", exc)
        return f"[Error: {exc}]"
