#!/bin/sh
# Emits SessionStart hook output for Claude Code telling the session to
# behave as a chat-bus agent. Also pre-registers the agent server-side so
# it appears on the bus deterministically — not dependent on the model
# choosing to call chat_register on its first turn.
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
INSTRUCTION_FILE="$HERE/chat-agent-instruction.txt"

# Pre-register server-side. Non-blocking: if the bus is down or the DB
# isn't writable, the session still starts; the model can try chat_register
# itself as a fallback (see instruction text).
"$HERE/chat-register-self.py" >&2 || true

if [ ! -r "$INSTRUCTION_FILE" ]; then
    echo "chat-session-start-hook: missing $INSTRUCTION_FILE" >&2
    exit 0
fi
exec python3 -c 'import json,sys; sys.stdout.write(json.dumps({"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":open(sys.argv[1]).read()}}))' "$INSTRUCTION_FILE"
