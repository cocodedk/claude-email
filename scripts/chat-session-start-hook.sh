#!/bin/sh
# Emits SessionStart hook output for Claude Code telling the session to
# behave as a chat-bus agent. Reads the instruction body from a sibling
# file so it stays under version control and can be edited without
# rewriting shell-embedded JSON.
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
INSTRUCTION_FILE="$HERE/chat-agent-instruction.txt"
if [ ! -r "$INSTRUCTION_FILE" ]; then
    echo "chat-session-start-hook: missing $INSTRUCTION_FILE" >&2
    exit 0
fi
jq -nc --rawfile ctx "$INSTRUCTION_FILE" \
    '{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $ctx}}'
