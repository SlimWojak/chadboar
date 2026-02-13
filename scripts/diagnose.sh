#!/usr/bin/env bash
# ChadBoar Diagnostic Script — Run on VPS to identify Telegram delivery issues.
# Usage: bash scripts/diagnose.sh
#
# This checks:
# 1. OpenClaw installation and version
# 2. openclaw.json config for correct xAI + Telegram setup
# 3. Gateway service status
# 4. Session health
# 5. Cron job status
# 6. Recent gateway logs for errors
# 7. Telegram bot token validity

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color
BOLD='\033[1m'

pass() { echo -e "${GREEN}[PASS]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
info() { echo -e "${BOLD}[INFO]${NC} $1"; }
section() { echo -e "\n${BOLD}═══ $1 ═══${NC}"; }

ISSUES=0

# ─── 1. OpenClaw Installation ──────────────────────────────────────

section "OpenClaw Installation"

if command -v openclaw &>/dev/null; then
    VERSION=$(openclaw --version 2>/dev/null || echo "unknown")
    pass "openclaw binary found: $VERSION"
else
    fail "openclaw not found in PATH"
    ISSUES=$((ISSUES + 1))
    echo "  Fix: npm install -g openclaw"
fi

# ─── 2. OpenClaw Config ────────────────────────────────────────────

section "OpenClaw Configuration"

OPENCLAW_DIR="$HOME/.openclaw"
CONFIG_FILE="$OPENCLAW_DIR/openclaw.json"

if [ -f "$CONFIG_FILE" ]; then
    pass "openclaw.json exists at $CONFIG_FILE"

    # Check for xAI provider config
    if grep -q '"xai"' "$CONFIG_FILE" 2>/dev/null; then
        pass "xAI provider found in config"
    elif grep -q '"openrouter"' "$CONFIG_FILE" 2>/dev/null; then
        warn "Config uses OpenRouter, not xAI directly"
        echo "  If you want direct xAI: set provider to 'xai' with baseUrl 'https://api.x.ai/v1'"
    else
        fail "No recognized model provider in config"
        ISSUES=$((ISSUES + 1))
    fi

    # Check model configuration
    if grep -qi 'grok' "$CONFIG_FILE" 2>/dev/null; then
        MODEL=$(grep -o '"primary"[[:space:]]*:[[:space:]]*"[^"]*"' "$CONFIG_FILE" 2>/dev/null | head -1)
        pass "Grok model configured: $MODEL"
    else
        fail "No Grok model found in config"
        ISSUES=$((ISSUES + 1))
    fi

    # Check Telegram config
    if grep -q '"botToken"' "$CONFIG_FILE" 2>/dev/null; then
        pass "Telegram botToken field present"
        # Check if it's a placeholder
        if grep -q 'TELEGRAM_BOT_TOKEN' "$CONFIG_FILE" 2>/dev/null; then
            fail "Telegram botToken is still a placeholder (\${TELEGRAM_BOT_TOKEN})"
            ISSUES=$((ISSUES + 1))
        fi
    else
        fail "No Telegram botToken in config"
        ISSUES=$((ISSUES + 1))
    fi

    # Check allowFrom
    if grep -q '"allowFrom"' "$CONFIG_FILE" 2>/dev/null; then
        pass "Telegram allowFrom field present"
    else
        warn "No Telegram allowFrom configured — bot may ignore messages"
    fi

    # Check for heartbeat disabled (should be every: "0" for cron mode)
    if grep -q '"every"[[:space:]]*:[[:space:]]*"0"' "$CONFIG_FILE" 2>/dev/null; then
        pass "Native heartbeat disabled (using cron)"
    elif grep -q '"heartbeat"' "$CONFIG_FILE" 2>/dev/null; then
        warn "Native heartbeat may still be enabled — check 'every' field"
    fi

    # Check for models section with xAI
    if grep -q '"models"' "$CONFIG_FILE" 2>/dev/null; then
        pass "Models section exists in config"
    else
        warn "No explicit 'models' section — OpenClaw may not know how to route to xAI"
        echo "  Add: models: { providers: { xai: { baseUrl: 'https://api.x.ai/v1', api: 'openai-completions' } } }"
    fi
else
    fail "openclaw.json NOT FOUND at $CONFIG_FILE"
    ISSUES=$((ISSUES + 1))
    echo "  Expected at: $CONFIG_FILE"
    echo "  See docs/openclaw.json.template for the template"
fi

# ─── 3. Auth Profiles ──────────────────────────────────────────────

section "Auth Profiles"

AUTH_DIR="$OPENCLAW_DIR/agents/main"
AUTH_FILE="$AUTH_DIR/auth.json"

if [ -f "$AUTH_FILE" ]; then
    pass "auth.json exists"
    if grep -qi 'xai' "$AUTH_FILE" 2>/dev/null; then
        pass "xAI auth profile found"
    else
        warn "No xAI auth profile in auth.json"
        echo "  Run: openclaw auth to configure xAI API key"
    fi
else
    warn "No auth.json found at $AUTH_FILE"
    echo "  Run: openclaw auth to set up API keys"
fi

# Check for XAI_API_KEY in environment
if [ -n "${XAI_API_KEY:-}" ]; then
    KEY_PREVIEW="${XAI_API_KEY:0:8}..."
    pass "XAI_API_KEY set in environment: $KEY_PREVIEW"
else
    warn "XAI_API_KEY not in environment (may be in auth.json or config)"
fi

# ─── 4. Gateway Service ────────────────────────────────────────────

section "Gateway Service"

if systemctl --user is-active openclaw-gateway.service &>/dev/null; then
    pass "Gateway service is active"
elif systemctl --user is-active openclaw-gateway &>/dev/null; then
    pass "Gateway service is active (no .service suffix)"
else
    fail "Gateway service is NOT running"
    ISSUES=$((ISSUES + 1))
    echo "  Start: systemctl --user start openclaw-gateway.service"
fi

# Check gateway process
GATEWAY_PIDS=$(pgrep -f "openclaw.*gateway" 2>/dev/null || true)
if [ -n "$GATEWAY_PIDS" ]; then
    PID_COUNT=$(echo "$GATEWAY_PIDS" | wc -l)
    if [ "$PID_COUNT" -gt 1 ]; then
        warn "Multiple gateway processes detected ($PID_COUNT PIDs): $GATEWAY_PIDS"
        echo "  This can cause message delivery issues. Kill stale ones."
    else
        pass "Single gateway process running: PID $GATEWAY_PIDS"
    fi
else
    fail "No gateway process found"
    ISSUES=$((ISSUES + 1))
fi

# ─── 5. Cron Job ───────────────────────────────────────────────────

section "Cron Job Status"

if command -v openclaw &>/dev/null; then
    echo "--- openclaw cron list ---"
    openclaw cron list 2>/dev/null || warn "Failed to list cron jobs"
    echo ""
fi

# ─── 6. Session Health ─────────────────────────────────────────────

section "Session Health"

SESSIONS_DIR="$OPENCLAW_DIR/agents/main/sessions"
SESSIONS_INDEX="$SESSIONS_DIR/sessions.json"

if [ -f "$SESSIONS_INDEX" ]; then
    pass "sessions.json exists"
    # Show session count
    SESSION_COUNT=$(grep -c '"sessionId"' "$SESSIONS_INDEX" 2>/dev/null || echo "0")
    info "Sessions tracked: $SESSION_COUNT"
else
    warn "No sessions.json found"
fi

# Check for large session files (context accumulation)
if [ -d "$SESSIONS_DIR" ]; then
    LARGE_FILES=$(find "$SESSIONS_DIR" -name "*.jsonl" -size +1M 2>/dev/null || true)
    if [ -n "$LARGE_FILES" ]; then
        warn "Large session files detected (possible context accumulation):"
        echo "$LARGE_FILES" | while read -r f; do
            SIZE=$(du -h "$f" 2>/dev/null | cut -f1)
            echo "    $f ($SIZE)"
        done
    else
        pass "No oversized session files"
    fi
fi

# ─── 7. Recent Gateway Logs ────────────────────────────────────────

section "Recent Gateway Logs (last 50 lines)"

if journalctl --user -u openclaw-gateway.service --no-pager -n 50 2>/dev/null; then
    :
else
    warn "Could not read gateway logs"
    echo "  Try: journalctl --user -u openclaw-gateway.service --no-pager -n 50"
fi

# Check for specific error patterns
section "Error Pattern Scan"

if journalctl --user -u openclaw-gateway.service --no-pager -n 500 2>/dev/null | grep -i "error\|fail\|reject\|timeout\|abort" | tail -10; then
    warn "Errors found in recent logs (see above)"
else
    pass "No obvious errors in recent logs"
fi

# Check for suppression tokens in logs
if journalctl --user -u openclaw-gateway.service --no-pager -n 500 2>/dev/null | grep -i "silent.*true\|HEARTBEAT_OK\|NO_REPLY\|suppress" | tail -5; then
    warn "Suppression tokens detected in logs — responses may be silently dropped"
    ISSUES=$((ISSUES + 1))
else
    pass "No suppression tokens in recent logs"
fi

# ─── 8. Telegram Bot Check ─────────────────────────────────────────

section "Telegram Bot Check"

# Try to extract bot token from config
if [ -f "$CONFIG_FILE" ]; then
    BOT_TOKEN=$(grep -o '"botToken"[[:space:]]*:[[:space:]]*"[^"]*"' "$CONFIG_FILE" 2>/dev/null | grep -o '"[^"]*"$' | tr -d '"' || true)
    if [ -n "$BOT_TOKEN" ] && [[ "$BOT_TOKEN" != *'${'* ]]; then
        # Test the bot token
        RESPONSE=$(curl -s "https://api.telegram.org/bot${BOT_TOKEN}/getMe" 2>/dev/null || true)
        if echo "$RESPONSE" | grep -q '"ok":true'; then
            BOT_NAME=$(echo "$RESPONSE" | grep -o '"username":"[^"]*"' | grep -o '[^"]*"$' | tr -d '"')
            pass "Telegram bot is valid: @$BOT_NAME"
        else
            fail "Telegram bot token is invalid"
            ISSUES=$((ISSUES + 1))
            echo "  Response: $RESPONSE"
        fi
    else
        info "Could not extract bot token from config (may use env var)"
    fi
fi

# ─── 9. xAI API Check ──────────────────────────────────────────────

section "xAI API Check"

XAI_KEY="${XAI_API_KEY:-}"
if [ -z "$XAI_KEY" ] && [ -f "$AUTH_FILE" ]; then
    XAI_KEY=$(grep -o '"key"[[:space:]]*:[[:space:]]*"[^"]*"' "$AUTH_FILE" 2>/dev/null | head -1 | grep -o '"[^"]*"$' | tr -d '"' || true)
fi

if [ -n "$XAI_KEY" ]; then
    RESPONSE=$(curl -s -w "\n%{http_code}" "https://api.x.ai/v1/models" \
        -H "Authorization: Bearer $XAI_KEY" \
        -H "Content-Type: application/json" 2>/dev/null || true)
    HTTP_CODE=$(echo "$RESPONSE" | tail -1)
    BODY=$(echo "$RESPONSE" | head -n -1)

    if [ "$HTTP_CODE" = "200" ]; then
        pass "xAI API key is valid (HTTP 200)"
        # List available models
        echo "  Available Grok models:"
        echo "$BODY" | grep -o '"id":"[^"]*grok[^"]*"' | head -5 | sed 's/"id":"/ - /;s/"//' || true
    elif [ "$HTTP_CODE" = "401" ]; then
        fail "xAI API key is invalid (HTTP 401)"
        ISSUES=$((ISSUES + 1))
    else
        warn "xAI API returned HTTP $HTTP_CODE"
        echo "  $BODY" | head -5
    fi
else
    warn "Could not find xAI API key to test"
fi

# ─── Summary ────────────────────────────────────────────────────────

section "Summary"

if [ "$ISSUES" -eq 0 ]; then
    pass "No critical issues found"
else
    fail "$ISSUES critical issue(s) found — see [FAIL] items above"
fi

echo ""
info "If interactive replies are not reaching Telegram, check:"
echo "  1. Gateway logs for 'silent: true' or suppression tokens"
echo "  2. Model is producing text output (not just tool calls)"
echo "  3. openclaw.json has correct model config: xai/grok-4-1-fast"
echo "  4. Session isn't collapsed (large .jsonl files, short outputs)"
echo "  5. Bot token is correct and bot is added to the channel/group"
echo ""
info "Quick fixes to try:"
echo "  1. Restart gateway: systemctl --user restart openclaw-gateway.service"
echo "  2. Reset session: openclaw sessions --reset agent:main:main"
echo "  3. Check health: openclaw health"
echo "  4. Test bot: curl https://api.telegram.org/bot<TOKEN>/getMe"
