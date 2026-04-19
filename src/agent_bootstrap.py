"""Per-project bootstrap helpers — MCP config, Claude settings, hook wiring."""
import json
import logging
import os

logger = logging.getLogger(__name__)

_CHAT_MCP_SERVER_NAME = "claude-chat"
_HOOK_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts", "chat-session-start-hook.sh",
)


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
    """Read-merge-write .mcp.json so existing MCP servers are preserved."""
    mcp_path = os.path.join(project_dir, ".mcp.json")

    try:
        with open(mcp_path, "r") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {}

    servers = data.setdefault("mcpServers", {})
    servers[_CHAT_MCP_SERVER_NAME] = {"type": "sse", "url": chat_url}

    with open(mcp_path, "w") as f:
        json.dump(data, f, indent=2)

    logger.info("Wrote MCP config to %s", mcp_path)


def inject_session_start_hook(project_dir: str, hook_script_path: str) -> None:
    """Write .claude/settings.json so each session in project_dir invokes the
    SessionStart hook at hook_script_path. Merges with any existing settings.

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
