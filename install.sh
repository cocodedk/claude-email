#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER_SYSTEMD_DIR="$HOME/.config/systemd/user"

echo "==> Claude Email + Chat — installer"

# --- Prerequisites ---
echo "==> Checking prerequisites..."

if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found"
    exit 1
fi

if ! command -v claude &>/dev/null; then
    echo "ERROR: claude CLI not found — install it first"
    exit 1
fi

# --- .env check ---
if [[ ! -f "$SCRIPT_DIR/.env" ]]; then
    echo "ERROR: .env not found — copy .env.example and fill it in:"
    echo "  cp $SCRIPT_DIR/.env.example $SCRIPT_DIR/.env"
    exit 1
fi

required_vars=(IMAP_HOST IMAP_PORT SMTP_HOST SMTP_PORT EMAIL_ADDRESS EMAIL_PASSWORD AUTHORIZED_SENDER)
missing=()
for var in "${required_vars[@]}"; do
    if ! grep -q "^${var}=" "$SCRIPT_DIR/.env"; then
        missing+=("$var")
    fi
done
if [[ ${#missing[@]} -gt 0 ]]; then
    echo "ERROR: missing required vars in .env: ${missing[*]}"
    exit 1
fi

echo "    .env OK"

# --- Virtual environment ---
echo "==> Setting up Python virtual environment..."
python3 -m venv "$SCRIPT_DIR/.venv"
"$SCRIPT_DIR/.venv/bin/pip" install --quiet -r "$SCRIPT_DIR/requirements.txt"
echo "    venv OK"

# --- Remove old system-level services if present ---
for svc in claude-email claude-chat; do
    if [[ -f "/etc/systemd/system/$svc.service" ]]; then
        echo "==> Migrating $svc from system-level to user-level..."
        sudo systemctl stop "$svc" 2>/dev/null || true
        sudo systemctl disable "$svc" 2>/dev/null || true
        sudo rm -f "/etc/systemd/system/$svc.service"
        echo "    old $svc system service removed"
    fi
done
if [[ -f "/etc/systemd/system/claude-email.service" ]] || [[ -f "/etc/systemd/system/claude-chat.service" ]]; then
    sudo systemctl daemon-reload
fi

# --- Enable lingering (one-time, requires sudo) ---
if ! loginctl show-user "$(whoami)" -p Linger 2>/dev/null | grep -q "yes"; then
    echo "==> Enabling lingering for $(whoami) (one-time sudo)..."
    sudo loginctl enable-linger "$(whoami)"
    echo "    lingering enabled"
fi

# --- Install user-level systemd services ---
echo "==> Installing user-level systemd services..."
mkdir -p "$USER_SYSTEMD_DIR"

# claude-chat first (claude-email depends on it)
cp "$SCRIPT_DIR/claude-chat.service" "$USER_SYSTEMD_DIR/"
cp "$SCRIPT_DIR/claude-email.service" "$USER_SYSTEMD_DIR/"
systemctl --user daemon-reload

systemctl --user enable claude-chat
systemctl --user restart claude-chat
echo "    claude-chat enabled and started"

systemctl --user enable claude-email
systemctl --user restart claude-email
echo "    claude-email enabled and started"

# --- Status ---
echo ""
echo "==> Service status:"
systemctl --user status claude-chat --no-pager || true
echo ""
systemctl --user status claude-email --no-pager || true
echo ""
echo "==> Done."
echo "==> Logs:   journalctl --user -u claude-chat -f"
echo "==>         journalctl --user -u claude-email -f"
echo "==> Manage: systemctl --user {start|stop|restart|status} claude-{chat,email}"
