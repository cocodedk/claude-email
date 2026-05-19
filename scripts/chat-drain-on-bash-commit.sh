#!/bin/sh
# PostToolUse drain wrapper — fires the chat-drain only on `git commit`.
#
# Wired in via `inject_session_start_hook` so every Claude Code project
# auto-drains the bus inbox immediately after each commit, closing the
# "peer pinged me while I was mid-edit" gap without polling.
#
# Reads the PostToolUse payload from stdin, extracts `.tool_input.command`,
# matches a leading `git commit` token (no submatcher inside `Bash`), and
# pipes the original payload through to `chat-drain-inbox.py`. Non-commit
# Bash invocations exit silently.
set -u

payload=$(cat)
cmd=$(printf '%s' "$payload" | jq -r '.tool_input.command // ""' 2>/dev/null || echo "")
if ! printf '%s' "$cmd" | grep -qE '^[[:space:]]*git[[:space:]]+commit($|[[:space:]])'; then
    exit 0
fi

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
printf '%s' "$payload" | "$SCRIPT_DIR/chat-drain-inbox.py"
