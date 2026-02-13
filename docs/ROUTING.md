# AutistBoar — Model Routing

## Current Setup

### Interactive Chat (Telegram)
**Session:** main  
**Model:** `openrouter/anthropic/claude-sonnet-4.5`  
**Use Case:** G's interactive assistant, personality-driven responses, complex reasoning  
**Cost:** ~$3/1M input tokens

### Autonomous Heartbeat (Native)
**Session:** main (shared with chat, heartbeat polls inject into same context)  
**Model:** `openrouter/deepseek/deepseek-chat`  
**Schedule:** Every 10 minutes  
**Use Case:** Execute HEARTBEAT.md checklist, structured decision-making  
**Cost:** ~$0.30/1M input tokens, ~$0.12/1M output tokens (10x cheaper than Sonnet)

## Native Heartbeat Configuration

Configured via `agents.defaults.heartbeat` in openclaw.json:

```yaml
every: "10m"
model: "openrouter/deepseek/deepseek-chat"
session: "main"
target: "none"  # No delivery unless alerts
prompt: "Read HEARTBEAT.md..."
ackMaxChars: 100  # Suppress long HEARTBEAT_OK responses
```

## Why This Split?

- **Sonnet** excels at personality, wit, complex multi-step reasoning → ideal for chat
- **DeepSeek** is cheap, fast, good at structured tasks → ideal for HEARTBEAT.md execution
- **Cost savings:** ~$2.70/1M tokens saved per heartbeat cycle
- **Shared session:** Heartbeat updates persist in chat history (checkpoint.md visible to both)

## ⚠️ CRITICAL: Do NOT Use Cron for Heartbeats

**Native heartbeats** (`agents.defaults.heartbeat` in openclaw.json) are the CORRECT pattern.

**Cron jobs** (`cron` tool) are for:
- Scheduled reminders
- Wake events  
- One-off tasks

**DO NOT use cron for heartbeats.** Creating a cron job for heartbeats causes:
- Redundant triggering (both native + cron fire)
- Model selection conflicts (cron hits Sonnet, not DeepSeek)
- Increased costs (~10x per cycle)
- Confusion and "reminder content not found" errors

**Rule:** If you think heartbeats aren't working, check `openclaw.json` config first. Do NOT create a cron job.

## Fallback Strategy

If DeepSeek fails on heartbeat, OpenClaw skips that cycle and retries in 10 minutes. No automatic fallback to prevent Sonnet bleed.

## Changing Models

To update the heartbeat model, edit `~/.openclaw/openclaw.json`:

```json
"agents": {
  "defaults": {
    "heartbeat": {
      "model": "openrouter/zhipu/glm-4-flash"  // Or any model in the allowlist
    }
  }
}
```

Then apply: `openclaw gateway config.apply`

To update the chat model, edit `agents.defaults.model.primary` in the same file.

## Cost Tracking

Monitor spend via OpenRouter dashboard: https://openrouter.ai/activity

Expected usage:
- **Heartbeat:** ~144 cycles/day × ~18K tokens/cycle = 2.6M tokens/day × $0.30/1M = **$0.78/day**
- **Chat:** Variable, depends on G's activity. Assume 10 interactions/day × 50K tokens = 500K tokens/day × $3/1M = **$1.50/day**

**Total estimated:** ~$2.30/day or ~$70/month

**Actual observed (first heartbeat):**
- DeepSeek heartbeat: $0.0105 per cycle → $1.51/day (144 cycles)
- Savings vs Sonnet: ~$4.46/day (~$135/month)
