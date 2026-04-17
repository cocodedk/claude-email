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
    """
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
) -> tuple[str, int]:
    """Spawn a Claude CLI agent in the given project directory.

    Returns (agent_name, pid).
    Raises ValueError if the path is invalid or outside allowed_base.
    """
    project_dir = validate_project_path(project_dir, allowed_base)
    name = build_agent_name(project_dir)
    inject_mcp_config(project_dir, chat_url)

    cmd = [claude_bin]
    if instruction:
        cmd += ["--print", instruction]

    proc = subprocess.Popen(
        cmd, cwd=project_dir, shell=False,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    db.register_agent(name, project_dir)
    db.update_agent_pid(name, proc.pid)

    logger.info("Spawned agent %s (PID %d) in %s", name, proc.pid, project_dir)
    return name, proc.pid
