"""Agent spawner — builds names, injects MCP config, spawns Claude CLI agents."""
import json
import logging
import os
import subprocess
from pathlib import Path, PurePosixPath

logger = logging.getLogger(__name__)


def build_agent_name(project_path: str) -> str:
    """Extract folder name from path, prefix with 'agent-'.

    Handles trailing slashes: '/home/user/fits/' → 'agent-fits'
    """
    folder = PurePosixPath(project_path).name
    return f"agent-{folder}"


def inject_mcp_config(project_dir: str, chat_url: str) -> None:
    """Read-merge-write .mcp.json so existing MCP servers are preserved."""
    mcp_path = os.path.join(project_dir, ".mcp.json")

    try:
        with open(mcp_path, "r") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {}

    servers = data.setdefault("mcpServers", {})
    servers["claude-chat"] = {"url": chat_url}

    with open(mcp_path, "w") as f:
        json.dump(data, f, indent=2)

    logger.info("Wrote MCP config to %s", mcp_path)


def validate_project_path(project_dir: str, allowed_base: str | None = None) -> str:
    """Canonicalize and validate a project directory path.

    Returns the resolved absolute path.
    Raises ValueError if the path is invalid or outside the allowed base.
    Non-absolute inputs are resolved relative to allowed_base when set, so
    email commands can spell `spawn babakcast` instead of the full path.
    """
    if allowed_base and not os.path.isabs(project_dir):
        project_dir = os.path.join(allowed_base, project_dir)
    resolved = str(Path(project_dir).resolve())
    if not os.path.isdir(resolved):
        raise ValueError(f"Directory does not exist: {resolved}")
    if allowed_base:
        base = str(Path(allowed_base).resolve())
        if not resolved.startswith(base + os.sep) and resolved != base:
            raise ValueError(f"Path {resolved} is outside allowed base {base}")
    return resolved


def spawn_agent(
    db,
    project_dir: str,
    chat_url: str,
    instruction: str = "",
    claude_bin: str = "claude",
    allowed_base: str | None = None,
    yolo: bool = False,
    extra_env: dict[str, str] | None = None,
    model: str | None = None,
    effort: str | None = None,
    max_budget_usd: str | None = None,
) -> tuple[str, int]:
    """Spawn a Claude CLI agent in the given project directory.

    Returns (agent_name, pid).
    Raises ValueError if the path is invalid or outside allowed_base.
    When yolo is True, appends --dangerously-skip-permissions.
    extra_env is merged over os.environ for the spawned process.
    """
    project_dir = validate_project_path(project_dir, allowed_base)
    name = build_agent_name(project_dir)
    inject_mcp_config(project_dir, chat_url)

    cmd = [claude_bin]
    if yolo:
        cmd.append("--dangerously-skip-permissions")
    if model:
        cmd += ["--model", model]
    if effort:
        cmd += ["--effort", effort]
    if instruction:
        cmd += ["--print", instruction]
        if max_budget_usd:
            cmd += ["--max-budget-usd", max_budget_usd]
    elif max_budget_usd:
        logger.info(
            "max_budget_usd set but no instruction supplied — skipping --max-budget-usd"
            " (only applies when --print is used)",
        )

    env = {**os.environ, **extra_env} if extra_env else None
    proc = subprocess.Popen(
        cmd, cwd=project_dir, shell=False,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=env,
    )

    db.register_agent(name, project_dir)
    db.update_agent_pid(name, proc.pid)

    logger.info("Spawned agent %s (PID %d) in %s", name, proc.pid, project_dir)
    return name, proc.pid
