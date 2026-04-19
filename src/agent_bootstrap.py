"""Per-project bootstrap helpers — MCP config, Claude settings, hook wiring."""
import json
import logging
import os

logger = logging.getLogger(__name__)

CHAT_MCP_SERVER_NAME = "claude-chat"
HOOK_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts", "chat-session-start-hook.sh",
)


def _load_json_dict(path: str) -> dict:
    """Read a JSON object from path. Return {} if missing, corrupt, or not an object."""
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: str, data: dict) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


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
        data = _load_json_dict(cfg_path)
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
            _write_json(cfg_path, data)
            logger.info(
                "Approved MCP server %r for project %s in %s",
                server_name, project_dir, cfg_path,
            )
    except OSError as exc:
        logger.warning(
            "Could not write MCP approval to %s: %s — agent will need manual /mcp approval",
            cfg_path, exc,
        )


def inject_mcp_config(project_dir: str, chat_url: str) -> None:
    mcp_path = os.path.join(project_dir, ".mcp.json")
    data = _load_json_dict(mcp_path)
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        servers = data["mcpServers"] = {}
    servers[CHAT_MCP_SERVER_NAME] = {"type": "sse", "url": chat_url}
    _write_json(mcp_path, data)
    logger.info("Wrote MCP config to %s", mcp_path)


def inject_session_start_hook(project_dir: str, hook_script_path: str) -> None:
    """Write .claude/settings.json pointing its SessionStart hook at hook_script_path.

    hook_script_path MUST be absolute — Claude Code resolves hook commands
    from the session cwd, not the repo root.
    """
    if not os.path.isabs(hook_script_path):
        raise ValueError(
            f"hook_script_path must be absolute; got {hook_script_path!r}"
        )
    settings_dir = os.path.join(project_dir, ".claude")
    settings_path = os.path.join(settings_dir, "settings.json")
    os.makedirs(settings_dir, exist_ok=True)
    data = _load_json_dict(settings_path)
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = data["hooks"] = {}
    hooks["SessionStart"] = [{
        "matcher": "startup|resume",
        "hooks": [{"type": "command", "command": hook_script_path}],
    }]
    _write_json(settings_path, data)
    logger.info("Wrote SessionStart hook to %s", settings_path)
