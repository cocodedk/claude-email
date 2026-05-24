"""Codex chat-bus bootstrap — writes .codex/ config, hooks, and wrapper script."""
import json
import logging
import os
import stat
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger(__name__)

_SCRIPTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts",
)

_CONFIG_TOML = """\
hooks = ".codex/hooks.json"

[mcp_servers.claude-chat]
url = "{mcp_url}"

[features]
hooks = true
"""

_HOOKS_JSON = {
    "hooks": {
        event: [{"hooks": [{
            "type": "command",
            "command": f"bash .codex/scripts/chat-agent-advisor-hook.sh {event}",
            "timeout": 10,
        }]}]
        for event in ("SessionStart", "UserPromptSubmit", "Stop", "PostToolUse", "PostCompact")
    },
}

_WRAPPER_SCRIPT = """\
#!/usr/bin/env bash
set -euo pipefail

event="${{1:-UserPromptSubmit}}"
chat_root="${{CHAT_ROOT:-{chat_root}}}"
agent_name="${{CODEX_CHAT_AGENT_NAME:-{agent_name}}}"
cat >/dev/null || true
hook_payload='{{"hook_event_name":"'"$event"'"}}'

export CLAUDE_AGENT_NAME="$agent_name"
export CLAUDE_PROCESS_MARKER="${{CODEX_PROCESS_MARKER:-codex}}"

if [[ ! -x "$chat_root/scripts/chat-register-self.py" ]]; then
  echo "chat-agent-advisor-hook: missing $chat_root/scripts/chat-register-self.py" >&2
  exit 0
fi

printf '%s' "$hook_payload" | "$chat_root/scripts/chat-register-self.py" >&2 || true

if [[ -x "$chat_root/scripts/chat-drain-inbox.py" ]]; then
  printf '%s' "$hook_payload" | "$chat_root/scripts/chat-drain-inbox.py" || true
fi
"""


def _sse_to_mcp_url(sse_url: str) -> str:
    parsed = urlparse(sse_url)
    return urlunparse(parsed._replace(path="/mcp/"))


def inject_codex_config(
    project_dir: str, chat_url: str, agent_name: str,
) -> None:
    """Write .codex/ config so Codex sessions can join the chat bus."""
    codex_dir = os.path.join(project_dir, ".codex")
    scripts_dir = os.path.join(codex_dir, "scripts")
    try:
        os.makedirs(scripts_dir, exist_ok=True)

        mcp_url = _sse_to_mcp_url(chat_url)
        with open(os.path.join(codex_dir, "config.toml"), "w", encoding="utf-8") as f:
            f.write(_CONFIG_TOML.format(mcp_url=mcp_url))

        with open(os.path.join(codex_dir, "hooks.json"), "w", encoding="utf-8") as f:
            json.dump(_HOOKS_JSON, f, indent=2)
            f.write("\n")

        script_path = os.path.join(scripts_dir, "chat-agent-advisor-hook.sh")
        chat_root = os.path.dirname(_SCRIPTS)
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(_WRAPPER_SCRIPT.format(chat_root=chat_root, agent_name=agent_name))
        os.chmod(script_path, 0o755)

        with open(os.path.join(codex_dir, "agent-name"), "w", encoding="utf-8") as f:
            f.write(agent_name + "\n")
    except OSError as exc:
        logger.warning("Could not write Codex config to %s: %s", codex_dir, exc)
