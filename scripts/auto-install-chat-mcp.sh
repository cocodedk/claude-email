#!/bin/sh
# Idempotent auto-installer for the claude-chat MCP wiring.
#
# Wired as a user-scope SessionStart hook (~/.claude/settings.json) so any
# fresh Claude Code session in a project under $BASE picks up the chat-bus
# config on first run. Runs on every SessionStart — inject_mcp_config and
# inject_session_start_hook are idempotent so re-running is always safe.
# Skips only the claude-email source tree itself (its config is checked in).
#
# Always exits 0 — a missing venv or transient error must not block the
# session.
set -u

BASE=/home/cocodedk/0-projects
EMAIL_PROJECT=$BASE/claude-email
PROJ=${CLAUDE_PROJECT_DIR:-$PWD}

# Skip projects outside the managed base.
case "$PROJ" in
  "$BASE"/*) ;;
  *) exit 0 ;;
esac

# Skip the source project — its config is git-tracked.
[ "$PROJ" = "$EMAIL_PROJECT" ] && exit 0

# Bail early if the venv is absent — avoids a silent failed spawn on every
# SessionStart if the email project hasn't been installed yet.
[ -x "$EMAIL_PROJECT/.venv/bin/python" ] || exit 0

# Run the install via the email project's venv. Suppress output and any
# error — the goal is silent first-time wiring, not a chatty hook.
"$EMAIL_PROJECT/.venv/bin/python" - <<'PY' 2>/dev/null || true
import os, sys
sys.path.insert(0, "/home/cocodedk/0-projects/claude-email")
from src.agent_bootstrap import (
    CHAT_MCP_SERVER_NAME, HOOK_SCRIPT,
    approve_mcp_server_for_project, inject_mcp_config,
    inject_session_start_hook,
)
project = os.environ["CLAUDE_PROJECT_DIR"]
inject_mcp_config(project, "http://127.0.0.1:8420/sse")
inject_session_start_hook(project, HOOK_SCRIPT)
config_dir = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~")
approve_mcp_server_for_project(config_dir, project, CHAT_MCP_SERVER_NAME)
PY

exit 0
