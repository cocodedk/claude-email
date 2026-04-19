"""Agent spawner — builds names, injects MCP config, spawns Claude CLI agents."""
import json
import logging
import os
import subprocess
from pathlib import Path, PurePosixPath

logger = logging.getLogger(__name__)

_CHAT_MCP_SERVER_NAME = "claude-chat"


def build_agent_name(project_path: str) -> str:
    """Extract folder name from path, prefix with 'agent-'.

    Handles trailing slashes: '/home/user/fits/' → 'agent-fits'
    """
    folder = PurePosixPath(project_path).name
    return f"agent-{folder}"


def approve_mcp_server_for_project(
    config_dir: str, project_dir: str, server_name: str,
) -> None:
    """Pre-approve an .mcp.json server for a project in Claude Code's config.

    Without this, a freshly-spawned claude session silently ignores
    .mcp.json servers it hasn't been told to trust. We add server_name to
    projects[project_dir]['enabledMcpjsonServers'] in <config_dir>/.claude.json,
    creating the file/project entry as needed. Idempotent on repeat calls.
    """
    cfg_path = os.path.join(config_dir, ".claude.json")
    try:
        os.makedirs(config_dir, exist_ok=True)
        try:
            with open(cfg_path, "r") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}

        # Normalize shape: valid JSON can still have the wrong types
        # (e.g. projects: []) that would crash setdefault/append.
        if not isinstance(data, dict):
            data = {}
        if not isinstance(data.get("projects"), dict):
            data["projects"] = {}
        projects = data["projects"]
        if not isinstance(projects.get(project_dir), dict):
            projects[project_dir] = {}
        project_entry = projects[project_dir]
        if not isinstance(project_entry.get("enabledMcpjsonServers"), list):
            project_entry["enabledMcpjsonServers"] = []
        approved = project_entry["enabledMcpjsonServers"]
        if server_name not in approved:
            approved.append(server_name)
            with open(cfg_path, "w") as f:
                json.dump(data, f, indent=2)
            logger.info("Approved MCP server %r for project %s in %s", server_name, project_dir, cfg_path)
    except OSError as exc:
        logger.warning("Could not write MCP approval to %s: %s — agent will need manual /mcp approval", cfg_path, exc)


def inject_mcp_config(project_dir: str, chat_url: str) -> None:
    """Read-merge-write .mcp.json so existing MCP servers are preserved."""
    mcp_path = os.path.join(project_dir, ".mcp.json")

    try:
        with open(mcp_path, "r") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {}

    servers = data.setdefault("mcpServers", {})
    # Claude Code requires explicit transport type for network MCP servers;
    # without it the server is silently skipped at session start.
    servers["claude-chat"] = {"type": "sse", "url": chat_url}

    with open(mcp_path, "w") as f:
        json.dump(data, f, indent=2)

    logger.info("Wrote MCP config to %s", mcp_path)


def inject_session_start_hook(project_dir: str, hook_script_path: str) -> None:
    """Write .claude/settings.json so each session in project_dir invokes the
    SessionStart hook at hook_script_path. Merges with any existing settings.

    hook_script_path MUST be absolute — Claude Code resolves hook commands
    from the session cwd, not the repo root.
    """
    settings_dir = os.path.join(project_dir, ".claude")
    settings_path = os.path.join(settings_dir, "settings.json")
    os.makedirs(settings_dir, exist_ok=True)

    try:
        with open(settings_path, "r") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}

    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = data["hooks"] = {}
    hooks["SessionStart"] = [{
        "matcher": "startup|resume",
        "hooks": [{"type": "command", "command": hook_script_path}],
    }]

    with open(settings_path, "w") as f:
        json.dump(data, f, indent=2)

    logger.info("Wrote SessionStart hook to %s", settings_path)


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

    # Pre-approve claude-chat in the spawned agent's config dir so Claude Code
    # doesn't silently skip the injected .mcp.json on first launch.
    merged_env = {**os.environ, **(extra_env or {})}
    agent_config_dir = merged_env.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~")
    approve_mcp_server_for_project(agent_config_dir, project_dir, _CHAT_MCP_SERVER_NAME)

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
