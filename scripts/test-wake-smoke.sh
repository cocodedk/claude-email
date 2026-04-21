#!/usr/bin/env bash
# End-to-end smoke for the wake watcher.
# Requires: claude-chat.service running, CHAT_DB_PATH exported (or sourced
# from .env), and WAKE_USER_AVATAR_NAME matching the email relay's avatar
# (default "user"). The script registers a dummy agent, posts a message
# addressed to it, waits 15s, then prints the pending count.
set -euo pipefail

AGENT="${1:-agent-smoke}"
PROJECT_PATH="${2:-/tmp/smoke-wake}"

if [[ -f .env && -z "${CHAT_DB_PATH:-}" ]]; then
    # shellcheck disable=SC1091
    source .env
fi
: "${CHAT_DB_PATH:?CHAT_DB_PATH not set — export it or source .env}"

mkdir -p "$PROJECT_PATH"
echo ">> Registering $AGENT at $PROJECT_PATH"

AGENT="$AGENT" PROJECT_PATH="$PROJECT_PATH" .venv/bin/python - <<'PY'
import os
from src.chat_db import ChatDB
db = ChatDB(os.environ["CHAT_DB_PATH"])
agent = os.environ["AGENT"]
project = os.environ["PROJECT_PATH"]
db.register_agent(agent, project)
msg = db.insert_message("smoke-sender", agent, "wake test — please reply", "notify")
print(f"inserted message id {msg['id']}")
PY

echo ">> Waiting 15s for watcher to spawn + drain..."
sleep 15

AGENT="$AGENT" .venv/bin/python - <<'PY'
import os
from src.chat_db import ChatDB
rows = ChatDB(os.environ["CHAT_DB_PATH"]).get_pending_messages_for(os.environ["AGENT"])
print(f"pending after drain: {len(rows)}")
PY
