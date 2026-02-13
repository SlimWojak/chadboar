# AutistBoar — Operations Guide

## Starting the System

### Local Development
```bash
cd ~/chadboar
source .venv/bin/activate
openclaw gateway --verbose
```

### VPS Production
The gateway runs as a systemd service (installed by `openclaw onboard --install-daemon`).
```bash
# Check status
systemctl --user status openclaw

# View logs
openclaw logs --tail 50

# Restart
systemctl --user restart openclaw
```

## Monitoring

### Telegram
G's primary interface. AutistBoar sends:
- Trade alerts (entries and exits)
- Drawdown warnings
- Watchdog alerts (stop-loss, take-profit)
- Daily PnL summaries (via cron)

### Gateway Dashboard
```bash
# Local
open http://127.0.0.1:18789

# Remote (via Tailscale or SSH tunnel)
ssh -L 18789:127.0.0.1:18789 autistboar@<VPS_IP>
open http://127.0.0.1:18789
```

### Health Check
```bash
openclaw doctor
openclaw health
```

## Emergency Controls

### Kill Switch (Immediate Halt)
```bash
# Activate — stops all trading immediately
touch ~/chadboar/killswitch.txt

# With reason
echo "Manual halt — investigating anomaly" > ~/chadboar/killswitch.txt

# Deactivate — resume trading
rm ~/chadboar/killswitch.txt
```

### Gateway Stop
```bash
systemctl --user stop openclaw
```

### Verify Signer Isolation
```bash
cd ~/chadboar
source .venv/bin/activate
python3 -c "from lib.signer.keychain import verify_isolation; print(verify_isolation())"
```

## Cron Jobs (Post-Deploy)

### Daily PnL Summary (10 PM SGT)
```bash
openclaw cron add \
  --name "Daily PnL" \
  --cron "0 14 * * *" \
  --tz "Asia/Singapore" \
  --session isolated \
  --message "Read state/latest.md. Summarize today's trades, PnL, and notable signals. Be concise." \
  --model "openrouter/openrouter/auto" \
  --announce \
  --channel telegram \
  --to "<G_TELEGRAM_CHAT_ID>"
```

### Weekly Edge Review (Monday 9 AM SGT)
```bash
openclaw cron add \
  --name "Weekly Edge Review" \
  --cron "0 1 * * 1" \
  --tz "Asia/Singapore" \
  --session isolated \
  --message "Run: python3 -m lib.skills.bead_query --context 'weekly review'. Analyze which signal patterns led to wins vs losses this week. What should I do differently?" \
  --model "openrouter/anthropic/claude-sonnet-4-5" \
  --announce \
  --channel telegram \
  --to "<G_TELEGRAM_CHAT_ID>"
```

## Updating

```bash
# SSH to VPS
ssh autistboar@<VPS_IP>

# Pull latest code
cd ~/chadboar && git pull

# Update Python deps
source .venv/bin/activate && pip install -r requirements.txt

# Update OpenClaw
npm install -g openclaw@latest

# Verify
openclaw doctor --fix

# Restart
systemctl --user restart openclaw
```

## Logs

```bash
# OpenClaw logs
openclaw logs --tail 100

# System logs
journalctl --user -u openclaw -f

# Python skill output (when debugging)
cd ~/chadboar && source .venv/bin/activate
python3 -m lib.guards.killswitch
python3 -m lib.guards.drawdown
python3 -m lib.guards.risk
```

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Heartbeat not firing | `openclaw doctor`, check `heartbeat.every` in config |
| Telegram not responding | Verify `TELEGRAM_BOT_TOKEN` and `allowFrom` in config |
| Signer fails | Check `SIGNER_KEY_PATH`, verify file exists and is chmod 400 |
| API 429 errors | Check rate limits in `config/firehose.yaml`, backoff will retry |
| All RPC endpoints down | Check Helius status, system will use public RPC as fallback |
| Drawdown halt active | Wait 24h or manually update `state/state.json` (with caution) |
