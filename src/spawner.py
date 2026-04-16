"""Agent spawner — builds names, injects MCP config, spawns Claude CLI agents."""
import json
import logging
import os
import subprocess
from pathlib import PurePosixPath
from subprocess import PIPE

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


def spawn_agent(
    db,
    project_dir: str,
    chat_url: str,
    instruction: str = "",
    claude_bin: str = "claude",
) -> tuple[str, int]:
    """Spawn a Claude CLI agent in the given project directory.

    Returns (agent_name, pid).
    """
    name = build_agent_name(project_dir)
    inject_mcp_config(project_dir, chat_url)

    cmd = [claude_bin, "--print"]
    if instruction:
        cmd.append(instruction)

    proc = subprocess.Popen(cmd, cwd=project_dir, shell=False, stdout=PIPE, stderr=PIPE)

    db.register_agent(name, project_dir)
    db.update_agent_pid(name, proc.pid)

    logger.info("Spawned agent %s (PID %d) in %s", name, proc.pid, project_dir)
    return name, proc.pid
