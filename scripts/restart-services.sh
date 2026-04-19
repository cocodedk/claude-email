#!/bin/sh
# Restart claude-chat, claude-email, and (if installed) claude-chat-test.
# chat first (email depends on it via After=), then email. Optional test
# instance restarted alongside the primary chat. Verify all active units
# come up green before exiting non-zero.
set -eu

CHAT_UNIT="${SERVICE_NAME_CHAT:-claude-chat.service}"
EMAIL_UNIT="${SERVICE_NAME_EMAIL:-claude-email.service}"
TEST_UNIT="${SERVICE_NAME_CHAT_TEST:-claude-chat-test.service}"

units="$CHAT_UNIT $EMAIL_UNIT"
if systemctl --user cat "$TEST_UNIT" >/dev/null 2>&1; then
    units="$CHAT_UNIT $TEST_UNIT $EMAIL_UNIT"
fi

printf "Restarting %s...\n" "$units"
# shellcheck disable=SC2086
systemctl --user restart $units

sleep 2

fail=0
for u in $units; do
    s=$(systemctl --user is-active "$u")
    printf "  %s: %s\n" "$u" "$s"
    [ "$s" != "active" ] && fail=1
done
if [ "$fail" -eq 1 ]; then
    printf "FAIL: one or more services did not come up active\n" >&2
    exit 1
fi
