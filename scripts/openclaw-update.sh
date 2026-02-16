#!/bin/bash
# OpenClaw Auto-Updater — Runs daily via cron
# Checks for new OpenClaw releases and updates if available.
# Restarts the gateway after a successful update.
#
# Install: crontab -e → 0 4 * * * /home/autistboar/chadboar/scripts/openclaw-update.sh
#
# Runs at 04:00 UTC daily (low-activity window).

set -euo pipefail

LOCAL_PREFIX="/home/autistboar/.local"
INSTALL_DIR="$LOCAL_PREFIX/node_modules/openclaw"
LOG_FILE="/home/autistboar/chadboar/logs/openclaw-update.log"
LOCK_FILE="/tmp/openclaw-update.lock"
WORKSPACE="/home/autistboar/chadboar"

# Required for systemctl --user from cron
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR}/bus"

# Telegram alert (direct curl)
source "$WORKSPACE/.env"
TG_TOKEN="$TELEGRAM_BOT_TOKEN"
TG_CHAT="-1003795988066"

tg_alert() {
    local msg="$1"
    curl -s -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
        -d chat_id="$TG_CHAT" \
        -d text="$msg" \
        -d parse_mode="HTML" \
        > /dev/null 2>&1 || true
}

log() {
    local ts
    ts=$(date -u '+%Y-%m-%d %H:%M:%S UTC')
    echo "[$ts] $1" >> "$LOG_FILE"
}

# Locking
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    exit 0
fi

# Get current installed version
current_version=$(/usr/bin/node "$INSTALL_DIR/dist/index.js" --version 2>/dev/null || echo "unknown")

# Check latest version on npm
latest_version=$(npm view openclaw@latest version 2>/dev/null || echo "unknown")

if [ "$latest_version" = "unknown" ]; then
    log "ERROR: Could not fetch latest version from npm"
    exit 1
fi

if [ "$current_version" = "$latest_version" ]; then
    log "UP-TO-DATE: v${current_version}"
    exit 0
fi

log "UPDATE: v${current_version} -> v${latest_version}"

# Install the new version
if npm install --prefix "$LOCAL_PREFIX" "openclaw@${latest_version}" >> "$LOG_FILE" 2>&1; then
    # Verify installation
    new_version=$(/usr/bin/node "$INSTALL_DIR/dist/index.js" --version 2>/dev/null || echo "unknown")

    if [ "$new_version" = "$latest_version" ]; then
        log "INSTALL: Successfully installed v${new_version}"

        # Update systemd service description and version
        local service_file="$HOME/.config/systemd/user/openclaw-gateway.service"
        if [ -f "$service_file" ]; then
            sed -i "s/OpenClaw Gateway (v[^)]*)/OpenClaw Gateway (v${new_version})/" "$service_file"
            sed -i "s/OPENCLAW_SERVICE_VERSION=.*/OPENCLAW_SERVICE_VERSION=${new_version}/" "$service_file"
            systemctl --user daemon-reload 2>/dev/null || true
        fi

        # Restart gateway
        systemctl --user restart openclaw-gateway.service 2>/dev/null || true
        sleep 5

        if systemctl --user is-active --quiet openclaw-gateway.service 2>/dev/null; then
            log "RESTART: Gateway running on v${new_version}"
            tg_alert "$(printf '\xF0\x9F\xA6\x9E') OpenClaw updated: v${current_version} → v${new_version}. Gateway restarted OK."
        else
            log "RESTART: Gateway FAILED to start after update!"
            tg_alert "$(printf '\xF0\x9F\x94\xB4') OpenClaw update to v${new_version} — gateway FAILED to start! Manual check needed."
        fi
    else
        log "INSTALL: Version mismatch after install — expected ${latest_version}, got ${new_version}"
        tg_alert "$(printf '\xF0\x9F\x94\xB4') OpenClaw update failed — version mismatch after install."
    fi
else
    log "INSTALL: npm install failed for v${latest_version}"
    tg_alert "$(printf '\xF0\x9F\x94\xB4') OpenClaw update to v${latest_version} failed (npm error)."
fi

# Rotate log if >1MB
if [ -f "$LOG_FILE" ] && [ "$(stat -c%s "$LOG_FILE" 2>/dev/null || echo 0)" -gt 1048576 ]; then
    mv "$LOG_FILE" "${LOG_FILE}.old"
    log "Log rotated"
fi
