"""Per-project bootstrap helpers — MCP config, Claude settings, hook wiring."""
import json
import logging
import os

logger = logging.getLogger(__name__)

CHAT_MCP_SERVER_NAME = "claude-chat"
_SCRIPTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts",
)
HOOK_SCRIPT = os.path.join(_SCRIPTS, "chat-session-start-hook.sh")
DRAIN_SCRIPT = os.path.join(_SCRIPTS, "chat-drain-inbox.py")


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


def _merge_hook_event(
    hooks: dict, event: str, matcher: str, our_commands: list[str],
) -> None:
    """Ensure `event` has a matcher-block whose hook list contains exactly
    our_commands (in order) alongside any third-party commands already there.

    A command is considered "ours" if it lives under the claude-email
    scripts/ dir — so stale paths from a prior install layout are dropped
    on upgrade while genuine third-party hooks (arbitrary command paths)
    survive.
    """
    entries = hooks.get(event)
    if not isinstance(entries, list):
        entries = []
    kept: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for h in entry.get("hooks", []) if isinstance(entry.get("hooks"), list) else []:
            if (
                isinstance(h, dict)
                and h.get("type") == "command"
                and not _is_ours(h.get("command", ""))
            ):
                kept.append(h)
    new_hooks = [{"type": "command", "command": c} for c in our_commands] + kept
    hooks[event] = [{"matcher": matcher, "hooks": new_hooks}]


def _is_ours(command: str) -> bool:
    """A hook command is claude-email's if it points at a script whose
    basename matches our known script names. Prefix-based discrimination
    would mis-tag third-party paths that also live under similar roots,
    so match by basename instead.
    """
    base = os.path.basename(command)
    return base in {"chat-session-start-hook.sh", "chat-drain-inbox.py"}


def inject_session_start_hook(
    project_dir: str,
    hook_script_path: str,
    drain_script_path: str | None = None,
) -> None:
    """Write .claude/settings.json wiring the chat-bus hooks for this project.

    SessionStart (startup|resume): runs hook_script_path (pre-register + bus
    instruction) then drain_script_path (drains pre-existing queue into
    additionalContext).

    UserPromptSubmit: runs drain_script_path so every user turn auto-drains
    messages that arrived mid-session.

    Stop: runs drain_script_path to surface peer messages that arrived
    mid-response. The drain script emits {"decision":"block","reason":...}
    for the Stop event, cancelling the stop so the agent stays conversant
    without needing to poll chat_check_messages itself.

    Both paths MUST be absolute. drain_script_path defaults to DRAIN_SCRIPT
    (sibling of hook_script_path in the claude-email install).
    """
    if not os.path.isabs(hook_script_path):
        raise ValueError(
            f"hook_script_path must be absolute; got {hook_script_path!r}"
        )
    if drain_script_path is None:
        drain_script_path = DRAIN_SCRIPT
    if not os.path.isabs(drain_script_path):
        raise ValueError(
            f"drain_script_path must be absolute; got {drain_script_path!r}"
        )
    settings_dir = os.path.join(project_dir, ".claude")
    settings_path = os.path.join(settings_dir, "settings.json")
    os.makedirs(settings_dir, exist_ok=True)
    data = _load_json_dict(settings_path)
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = data["hooks"] = {}
    _merge_hook_event(
        hooks, "SessionStart", "startup|resume",
        [hook_script_path, drain_script_path],
    )
    _merge_hook_event(
        hooks, "UserPromptSubmit", "",
        [drain_script_path],
    )
    _merge_hook_event(
        hooks, "Stop", "",
        [drain_script_path],
    )
    _write_json(settings_path, data)
    logger.info(
        "Wrote SessionStart + UserPromptSubmit + Stop hooks to %s",
        settings_path,
    )
