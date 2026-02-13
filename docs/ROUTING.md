# ChadBoar — Model Routing

## Current Setup (xAI Grok)

### Interactive Chat (Telegram)
**Session:** main
**Model:** `xai/grok-4-1-fast`
**Use Case:** G's interactive assistant, personality-driven responses, trading analysis
**Provider:** xAI direct (OpenAI-compatible API at https://api.x.ai/v1)

**How it works:**
1. G sends message on Telegram
2. OpenClaw gateway routes to xAI Grok
3. Grok responds with text
4. Gateway automatically sends text back to Telegram
5. **Agent does NOT call the message tool** — auto-reply handles delivery

### Autonomous Heartbeat (Cron)
**Session:** isolated (fresh per run)
**Model:** `xai/grok-4-1-fast`
**Schedule:** Every 10 minutes via `openclaw cron`
**Use Case:** Execute HEARTBEAT.md checklist, structured decision-making

**How it works:**
1. Cron triggers with isolated session
2. Agent reads HEARTBEAT.md, executes checklist
3. Agent outputs report as plain text
4. Cron `--announce` delivers text to Telegram channel

## Why Cron, Not Native Heartbeat?

Native heartbeat accumulates session context across cycles. This caused model
collapse (responding with abbreviated output after 1-2 successful runs).
`openclaw cron` with `--session isolated` creates a fresh session per run.

## Delivery Rules

- **Interactive mode:** Just output text. Gateway delivers automatically.
  Do NOT call the message tool — it causes suppression of the auto-reply.
- **Heartbeat mode:** Just output text. Cron announce delivers automatically.
  Do NOT call the message tool — Grok formats tool args differently, causing
  "Action send requires a target" errors.
- **Cross-channel alerts:** Use the message tool ONLY when sending to a
  channel DIFFERENT from the one you received the message from.

## Suppression Tokens (NEVER OUTPUT THESE)

- `HEARTBEAT_OK` — causes gateway to mark output as silent
- `NO_REPLY` — causes gateway to suppress the response

These tokens prevent Telegram delivery. Never include them in any output.

## Configuration

Model config in `~/.openclaw/openclaw.json`:

```json
{
  "agents": {
    "defaults": {
      "model": {
        "primary": "xai/grok-4-1-fast"
      }
    }
  },
  "models": {
    "providers": {
      "xai": {
        "baseUrl": "https://api.x.ai/v1",
        "api": "openai-completions",
        "models": [{
          "id": "grok-4-1-fast",
          "name": "Grok 4.1 FAST",
          "reasoning": true,
          "contextWindow": 131072,
          "maxTokens": 8192
        }]
      }
    }
  }
}
```

Auth: Run `openclaw auth` to configure xAI API key, or set `XAI_API_KEY` env var.
