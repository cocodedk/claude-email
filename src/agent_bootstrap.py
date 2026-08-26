"""Per-project bootstrap helpers — MCP config, Claude settings, hook wiring."""
import logging
import os

from src.hook_merge import _merge_hook_event
from src.json_file import load_json_dict as _load_json_dict
from src.json_file import write_json as _write_json

logger = logging.getLogger(__name__)

CHAT_MCP_SERVER_NAME = "claude-chat"
_SCRIPTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts",
)
HOOK_SCRIPT = os.path.join(_SCRIPTS, "chat-session-start-hook.sh")
DRAIN_SCRIPT = os.path.join(_SCRIPTS, "chat-drain-inbox.py")
PRECOMPACT_SCRIPT = os.path.join(_SCRIPTS, "chat-precompact-hook.py")
POSTTOOL_DRAIN_SCRIPT = os.path.join(_SCRIPTS, "chat-drain-on-bash-commit.sh")
STOP_HOOK_SCRIPT = os.path.join(_SCRIPTS, "chat-stop-hook.py")


def _resolve_script(path: str | None, default: str, name: str) -> str:
    if path is None:
        path = default
    if not os.path.isabs(path):
        raise ValueError(f"{name} must be absolute; got {path!r}")
    return path


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


def persist_agent_name(project_dir: str, name: str) -> None:
    """Write agent name to .claude/agent-name so hooks resolve it consistently."""
    agent_name_path = os.path.join(project_dir, ".claude", "agent-name")
    try:
        os.makedirs(os.path.dirname(agent_name_path), exist_ok=True)
        with open(agent_name_path, "w", encoding="utf-8") as f:
            f.write(name + "\n")
    except OSError as exc:
        logger.warning("Could not persist agent name to %s: %s", agent_name_path, exc)


def inject_mcp_config(project_dir: str, chat_url: str) -> None:
    mcp_path = os.path.join(project_dir, ".mcp.json")
    data = _load_json_dict(mcp_path)
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        servers = data["mcpServers"] = {}
    servers[CHAT_MCP_SERVER_NAME] = {"type": "sse", "url": chat_url}
    _write_json(mcp_path, data)
    logger.info("Wrote MCP config to %s", mcp_path)


def inject_session_start_hook(
    project_dir: str,
    hook_script_path: str,
    drain_script_path: str | None = None,
    precompact_script_path: str | None = None,
    posttool_drain_script_path: str | None = None,
    stop_hook_script_path: str | None = None,
) -> None:
    """Write .claude/settings.json wiring the chat-bus hooks for this project.

    SessionStart (all sources — empty matcher): runs hook_script_path
    (pre-register + bus instruction) then drain_script_path (drains
    pre-existing queue into additionalContext). Empty matcher catches
    every session source (startup, resume, clear, compact, continue) so
    the hook fires uniformly — the earlier ``startup|resume`` regex
    silently skipped ``compact``/``continue`` sessions, leaving the agent
    row stale until the next drain repaired it.

    UserPromptSubmit: runs drain_script_path so every user turn auto-drains
    messages that arrived mid-session.

    Stop: runs drain_script_path to surface peer messages that arrived
    mid-response. The drain script emits {"decision":"block","reason":...}
    for the Stop event, cancelling the stop so the agent stays conversant
    without needing to poll chat_check_messages itself.

    PreCompact: runs precompact_script_path to log a hook_precompact flow
    event so the dashboard's flow panel keeps pulsing across compaction.
    Best-effort telemetry — never blocks the session.

    PostToolUse (matcher="Bash"): runs posttool_drain_script_path, a thin
    shell wrapper that filters to leading ``git commit`` invocations only
    and pipes the payload through to the drain. Closes the "peer pinged
    me while I was mid-edit and I'm about to commit" gap without polling.

    All paths MUST be absolute. drain_script_path defaults to DRAIN_SCRIPT,
    precompact_script_path defaults to PRECOMPACT_SCRIPT, and
    posttool_drain_script_path defaults to POSTTOOL_DRAIN_SCRIPT (all
    siblings of hook_script_path in the claude-email install).
    """
    hook_script_path = _resolve_script(hook_script_path, HOOK_SCRIPT, "hook_script_path")
    drain_script_path = _resolve_script(drain_script_path, DRAIN_SCRIPT, "drain_script_path")
    precompact_script_path = _resolve_script(
        precompact_script_path, PRECOMPACT_SCRIPT, "precompact_script_path",
    )
    posttool_drain_script_path = _resolve_script(
        posttool_drain_script_path, POSTTOOL_DRAIN_SCRIPT, "posttool_drain_script_path",
    )
    stop_hook_script_path = _resolve_script(
        stop_hook_script_path, STOP_HOOK_SCRIPT, "stop_hook_script_path",
    )
    settings_dir = os.path.join(project_dir, ".claude")
    settings_path = os.path.join(settings_dir, "settings.local.json")
    os.makedirs(settings_dir, exist_ok=True)
    data = _load_json_dict(settings_path)
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = data["hooks"] = {}
    _merge_hook_event(
        hooks, "SessionStart", "",
        [hook_script_path, drain_script_path],
    )
    _merge_hook_event(
        hooks, "UserPromptSubmit", "",
        [drain_script_path],
    )
    _merge_hook_event(
        hooks, "Stop", "",
        [drain_script_path, stop_hook_script_path],
    )
    _merge_hook_event(
        hooks, "PreCompact", "",
        [precompact_script_path],
    )
    _merge_hook_event(
        hooks, "PostToolUse", "Bash",
        [posttool_drain_script_path],
    )
    _write_json(settings_path, data)
    logger.info(
        "Wrote SessionStart + UserPromptSubmit + Stop (drain+stop-hook) "
        "+ PreCompact + PostToolUse hooks to %s",
        settings_path,
    )
