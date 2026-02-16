#!/bin/bash
# ChadBoar Gateway Watchdog — Independent supervisor
# Runs every 5 minutes via system crontab (NOT OpenClaw cron).
# Checks gateway health, heartbeat staleness, session collapse.
# Auto-recovers and alerts to Telegram via direct curl.
#
# Install: crontab -e → */5 * * * * /home/autistboar/chadboar/scripts/watchdog.sh
#
# This is the supervisor, not the supervised. No AI. No gateway dependency.

set -euo pipefail

WORKSPACE="/home/autistboar/chadboar"
SESSIONS_DIR="$HOME/.openclaw/agents/main/sessions"
STATE_FILE="$WORKSPACE/state/state.json"
LOG_FILE="$WORKSPACE/logs/watchdog.log"
LOCK_FILE="/tmp/chadboar-watchdog.lock"

# Required for systemctl --user to work from cron
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR}/bus"

# Telegram config — direct curl, independent of gateway
source "$WORKSPACE/.env"
TG_TOKEN="$TELEGRAM_BOT_TOKEN"
TG_CHAT="-1003795988066"

# Thresholds — staleness must exceed 2 full heartbeat cycles (10min each)
# to avoid false restarts when heartbeat is mid-execution
HEARTBEAT_STALE_MINUTES=25
SESSION_MAX_FILES=20
SESSION_MAX_AGE_HOURS=12

# --- Locking (prevent overlapping runs) ---
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    exit 0  # Another watchdog is running, skip silently
fi

# --- Logging ---
log() {
    local ts
    ts=$(date -u '+%Y-%m-%d %H:%M:%S UTC')
    echo "[$ts] $1" >> "$LOG_FILE"
}

# --- Telegram alert (direct curl, no gateway dependency) ---
tg_alert() {
    local msg="$1"
    curl -s -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
        -d chat_id="$TG_CHAT" \
        -d text="$msg" \
        -d parse_mode="HTML" \
        > /dev/null 2>&1 || true
}

# --- Restart gateway ---
restart_gateway() {
    local reason="$1"
    log "RESTART: $reason"
    systemctl --user restart openclaw-gateway.service 2>/dev/null || true
    # Wait for gateway to come back
    sleep 5
    if systemctl --user is-active --quiet openclaw-gateway.service 2>/dev/null; then
        log "RESTART: Gateway came back OK"
        tg_alert "$(printf '\xF0\x9F\x9F\xA1') WATCHDOG: Gateway restarted — ${reason}"
    else
        log "RESTART: Gateway failed to start!"
        tg_alert "$(printf '\xF0\x9F\x94\xB4') WATCHDOG CRITICAL: Gateway restart FAILED — ${reason}"
    fi
}

# --- Session cleanup ---
prune_sessions() {
    local reason="$1"
    local count_before
    count_before=$(find "$SESSIONS_DIR" -name '*.jsonl' -not -name 'main.jsonl' 2>/dev/null | wc -l)

    if [ "$count_before" -eq 0 ]; then
        return
    fi

    # Delete sessions older than SESSION_MAX_AGE_HOURS
    local pruned_age=0
    while IFS= read -r f; do
        rm -f "$f"
        pruned_age=$((pruned_age + 1))
        log "PRUNE: Deleted old session $(basename "$f")"
    done < <(find "$SESSIONS_DIR" -name '*.jsonl' -not -name 'main.jsonl' -mmin +$((SESSION_MAX_AGE_HOURS * 60)) 2>/dev/null)

    # If still over limit, delete oldest until at cap
    local remaining
    remaining=$(find "$SESSIONS_DIR" -name '*.jsonl' -not -name 'main.jsonl' 2>/dev/null | wc -l)
    local pruned_cap=0
    if [ "$remaining" -gt "$SESSION_MAX_FILES" ]; then
        local to_delete=$((remaining - SESSION_MAX_FILES))
        while IFS= read -r f; do
            rm -f "$f"
            pruned_cap=$((pruned_cap + 1))
        done < <(find "$SESSIONS_DIR" -name '*.jsonl' -not -name 'main.jsonl' -printf '%T@ %p\n' 2>/dev/null | sort -n | head -n "$to_delete" | awk '{print $2}')
    fi

    local total_pruned=$((pruned_age + pruned_cap))
    if [ "$total_pruned" -gt 0 ]; then
        local count_after
        count_after=$(find "$SESSIONS_DIR" -name '*.jsonl' -not -name 'main.jsonl' 2>/dev/null | wc -l)
        log "PRUNE: Removed $total_pruned sessions ($count_before -> $count_after). Reason: $reason"
    fi
}

# ============================================================
# CHECK 1: Is the gateway process running?
# ============================================================
gateway_alive=true
if ! systemctl --user is-active --quiet openclaw-gateway.service 2>/dev/null; then
    gateway_alive=false
    log "CHECK: Gateway is DOWN"
    prune_sessions "gateway-down-recovery"
    restart_gateway "Gateway process not running"
fi

# ============================================================
# CHECK 2: Is the heartbeat stale?
# ============================================================
if [ "$gateway_alive" = true ] && [ -f "$STATE_FILE" ]; then
    # Extract last_heartbeat_time from state.json (portable, no jq dependency)
    last_hb=$(python3 -c "
import json, sys
try:
    s = json.load(open('$STATE_FILE'))
    print(s.get('last_heartbeat_time', ''))
except: print('')
" 2>/dev/null || echo "")

    if [ -n "$last_hb" ]; then
        # Calculate minutes since last heartbeat
        hb_epoch=$(python3 -c "
from datetime import datetime, timezone
t = '$last_hb'.replace('Z', '+00:00')
try:
    dt = datetime.fromisoformat(t)
except:
    # Handle format without timezone
    dt = datetime.strptime(t[:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
print(int(dt.timestamp()))
" 2>/dev/null || echo "0")

        now_epoch=$(date -u +%s)
        if [ "$hb_epoch" -gt 0 ]; then
            stale_minutes=$(( (now_epoch - hb_epoch) / 60 ))
            if [ "$stale_minutes" -ge "$HEARTBEAT_STALE_MINUTES" ]; then
                log "CHECK: Heartbeat stale — ${stale_minutes}min since last beat"
                prune_sessions "heartbeat-stale-${stale_minutes}min"
                restart_gateway "Heartbeat stale (${stale_minutes}min, threshold ${HEARTBEAT_STALE_MINUTES}min)"
            fi
        fi
    fi
fi

# ============================================================
# CHECK 3: Session collapse detection
# ============================================================
if [ "$gateway_alive" = true ] && [ -d "$SESSIONS_DIR" ]; then
    collapse_result=$(cd "$WORKSPACE" && .venv/bin/python3 -m lib.guards.session_health 2>/dev/null || echo '{"status":"ERROR"}')
    collapse_status=$(echo "$collapse_result" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status','CLEAR'))" 2>/dev/null || echo "CLEAR")

    if [ "$collapse_status" = "COLLAPSING" ]; then
        log "CHECK: Session collapse detected — auto-clearing sessions"

        # Clear all isolated sessions (keep main.jsonl)
        find "$SESSIONS_DIR" -name '*.jsonl' -not -name 'main.jsonl' -delete 2>/dev/null || true

        log "CHECK: Sessions cleared. Restarting gateway."
        restart_gateway "Session collapse detected — auto-cleared sessions"
    fi
fi

# ============================================================
# CHECK 4: Routine session pruning (even if healthy)
# ============================================================
if [ -d "$SESSIONS_DIR" ]; then
    file_count=$(find "$SESSIONS_DIR" -name '*.jsonl' -not -name 'main.jsonl' 2>/dev/null | wc -l)
    if [ "$file_count" -gt "$SESSION_MAX_FILES" ]; then
        prune_sessions "routine-cap-exceeded (${file_count} files)"
    else
        # Still prune old files even if under cap
        old_count=$(find "$SESSIONS_DIR" -name '*.jsonl' -not -name 'main.jsonl' -mmin +$((SESSION_MAX_AGE_HOURS * 60)) 2>/dev/null | wc -l)
        if [ "$old_count" -gt 0 ]; then
            prune_sessions "routine-age-expired (${old_count} old files)"
        fi
    fi
fi

# Rotate log if too large (>1MB)
if [ -f "$LOG_FILE" ] && [ "$(stat -c%s "$LOG_FILE" 2>/dev/null || echo 0)" -gt 1048576 ]; then
    mv "$LOG_FILE" "${LOG_FILE}.old"
    log "Log rotated"
fi
