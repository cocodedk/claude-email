#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="claude-email"
USER_SYSTEMD_DIR="$HOME/.config/systemd/user"

echo "==> Claude Email Agent — installer"

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

# --- Remove old system-level service if present ---
if [[ -f "/etc/systemd/system/$SERVICE_NAME.service" ]]; then
    echo "==> Migrating from system-level to user-level service..."
    sudo systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    sudo systemctl disable "$SERVICE_NAME" 2>/dev/null || true
    sudo rm -f "/etc/systemd/system/$SERVICE_NAME.service"
    sudo systemctl daemon-reload
    echo "    old system service removed"
fi

# --- Enable lingering (one-time, requires sudo) ---
if ! loginctl show-user "$(whoami)" -p Linger 2>/dev/null | grep -q "yes"; then
    echo "==> Enabling lingering for $(whoami) (one-time sudo)..."
    sudo loginctl enable-linger "$(whoami)"
    echo "    lingering enabled"
fi

# --- Install user-level systemd service ---
echo "==> Installing user-level systemd service..."
mkdir -p "$USER_SYSTEMD_DIR"
cp "$SCRIPT_DIR/$SERVICE_NAME.service" "$USER_SYSTEMD_DIR/"
systemctl --user daemon-reload
systemctl --user enable "$SERVICE_NAME"
systemctl --user restart "$SERVICE_NAME"
echo "    service enabled and started"

# --- Status ---
echo ""
systemctl --user status "$SERVICE_NAME" --no-pager
echo ""
echo "==> Done. Logs: journalctl --user -u $SERVICE_NAME -f"
echo "==> Manage: systemctl --user {start|stop|restart|status} $SERVICE_NAME"
