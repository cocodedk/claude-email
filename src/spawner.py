"""Agent spawner — builds names, spawns Claude CLI agents."""
import logging
import os
import subprocess
from pathlib import Path, PurePosixPath

from src.agent_bootstrap import (
    CHAT_MCP_SERVER_NAME,
    HOOK_SCRIPT,
    approve_mcp_server_for_project,
    inject_mcp_config,
    inject_session_start_hook,
)
from src.agent_name import ENV_VAR_NAME, validated_agent_name

__all__ = [
    "CHAT_MCP_SERVER_NAME",
    "HOOK_SCRIPT",
    "approve_mcp_server_for_project",
    "build_agent_name",
    "inject_mcp_config",
    "inject_session_start_hook",
    "spawn_agent",
    "validate_project_path",
]

logger = logging.getLogger(__name__)


def build_agent_name(project_path: str) -> str:
    """Extract folder name from path, prefix with 'agent-'.

    Handles trailing slashes: '/home/user/fits/' → 'agent-fits'.
    Falls back to 'agent-unknown' for empty/degenerate paths so the
    name is always a usable bus identifier.
    """
    folder = PurePosixPath(project_path).name or "unknown"
    return f"agent-{folder}"


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
    agent_name: str | None = None,
) -> tuple[str, int]:
    """Spawn a Claude CLI agent in the given project directory.

    Returns (agent_name, pid).
    Raises ValueError if the path is invalid or outside allowed_base.
    When yolo is True, appends --dangerously-skip-permissions.
    extra_env is merged over os.environ for the spawned process.
    agent_name overrides the cwd-derived default; invalid names fall
    back to the default with a stderr warning.
    """
    project_dir = validate_project_path(project_dir, allowed_base)
    default_name = build_agent_name(project_dir)
    name = validated_agent_name(agent_name, default_name)

    # Basename collision guard: two dirs sharing a basename (/work/app and
    # /backup/app) both resolve to agent-app. Without this check, the second
    # spawn would silently UPDATE the existing row's project_path (ON
    # CONFLICT DO UPDATE) once the first agent died, so every consumer keyed
    # on agent-app would start misrouting. Fail loud instead.
    existing = db.get_agent(name)
    if existing and existing.get("project_path") and existing["project_path"] != project_dir:
        raise ValueError(
            f"Agent name {name!r} is already registered for "
            f"{existing['project_path']!r}. Basename collision — rename one "
            f"of the directories so each project gets a distinct agent name."
        )

    inject_mcp_config(project_dir, chat_url)
    inject_session_start_hook(project_dir, HOOK_SCRIPT)

    child_env = {**os.environ, **(extra_env or {}), ENV_VAR_NAME: name}
    agent_config_dir = child_env.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~")
    approve_mcp_server_for_project(agent_config_dir, project_dir, CHAT_MCP_SERVER_NAME)

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

    proc = subprocess.Popen(
        cmd, cwd=project_dir, shell=False,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=child_env,
    )

    db.register_agent(name, project_dir)
    db.update_agent_pid(name, proc.pid)

    logger.info("Spawned agent %s (PID %d) in %s", name, proc.pid, project_dir)
    return name, proc.pid
