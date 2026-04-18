#!/bin/sh
# Restart the claude-chat and claude-email user-level systemd services.
# chat first (email depends on it via After=), then email. Verify both are
# active before exiting non-zero on failure.
set -eu

CHAT_UNIT="${SERVICE_NAME_CHAT:-claude-chat.service}"
EMAIL_UNIT="${SERVICE_NAME_EMAIL:-claude-email.service}"

printf "Restarting %s and %s...\n" "$CHAT_UNIT" "$EMAIL_UNIT"
systemctl --user restart "$CHAT_UNIT" "$EMAIL_UNIT"

# Give uvicorn a moment to bind before is-active checks
sleep 2

status_chat=$(systemctl --user is-active "$CHAT_UNIT")
status_email=$(systemctl --user is-active "$EMAIL_UNIT")

printf "  %s: %s\n" "$CHAT_UNIT" "$status_chat"
printf "  %s: %s\n" "$EMAIL_UNIT" "$status_email"

if [ "$status_chat" != "active" ] || [ "$status_email" != "active" ]; then
    printf "FAIL: one or more services did not come up active\n" >&2
    exit 1
fi
