# Forensics: Telegram Delivery Fix — 2026-02-13

Session: Claude Opus 4.6 via Claude Code CLI

## What Was Done

### 1. Git Cleanup
- Merged `cursor/chadboar-telegram-output-5fc8` (commit c8661d5, Opus/Cursor — 11 files, more thorough) into main
- Deleted duplicate branch `claude/fix-telegram-delivery-WD5aV` (commit 98b9f20 — 8 files, less thorough)
- Both branches fixed the same Telegram delivery bugs; kept the better one

### 2. Config Migration (openclaw.json)
- **Model**: Changed from `openrouter/x-ai/grok-4-fast` to direct `xai/grok-4-1-fast` with provider config (`baseUrl: https://api.x.ai/v1`)
- **Heartbeat**: Disabled native heartbeat (`every: "0"`), cron job handles it
- **Suppression tokens**: Removed `HEARTBEAT_OK` from heartbeat prompt

### 3. Root Cause: Telegram Inbound Failure
- **Node.js 22 changed `autoSelectFamily` default to `false`**
- Gateway logged `autoSelectFamily=false (default-node22)` — Telegram long-polling silently failed
- **Fix**: Added `channels.telegram.network.autoSelectFamily: true` to openclaw.json
- Gateway now logs `autoSelectFamily=true (config)` and processes inbound messages

### 4. Root Cause: Grok HTML-Encodes `&&`
- Grok outputs `&amp;&amp;` instead of `&&` in bash tool call arguments
- ALL heartbeat guard commands failed: `cd /home/autistboar/chadboar && .venv/bin/python3 -m ...`
- **Fix**: Created `/home/autistboar/chadboar/boar` wrapper script that does `cd` + `exec` internally
- Updated HEARTBEAT.md: all commands now use `/home/autistboar/chadboar/boar -m lib.guards.killswitch` (no `&&`)

### 5. Cron Delivery Channel
- Cron job defaulted to WhatsApp: `[cron:...] Unsupported channel: whatsapp`
- **Fix**: `openclaw cron edit <id> --channel telegram`

### 6. Telegram Group Session Reset
- Session `f834a387...` had 119 stale delivery-mirror messages (heartbeat reports only, 0 agent tokens)
- No interactive turns had ever completed in the session
- **Fix**: Generated new session UUID `5a03580f-eec5-4d7a-b8b0-4191aae0c4c4`, updated sessions.json with `systemSent: false`

## Current State

### Working
- API keys: all valid (Telegram bot, xAI, channel access)
- Gateway: running, `autoSelectFamily=true`, Telegram provider starting
- Cron heartbeat: running every 10m, delivering to Telegram channel
- Agent via CLI: responds correctly (`openclaw agent --message "..."`)
- Agent in Telegram session: responds with text (confirmed via `openclaw agent --session-id`)
- Delivery FROM bot to Telegram: confirmed (user received test message)

### Still Investigating
- **Interactive replies**: User sends message in Telegram group, agent processes it, but response may not route back. The delivery pipeline from inbound Telegram message -> agent turn -> response delivery needs end-to-end verification with a live Telegram message
- Possible that delivery context isn't being set correctly on inbound Telegram messages vs CLI-initiated sessions

## Key Files Modified
| File | Change |
|------|--------|
| `~/.openclaw/openclaw.json` | Model, provider, heartbeat, Telegram config, autoSelectFamily |
| `~/chadboar/boar` | **NEW** — wrapper script to avoid `&&` in bash commands |
| `~/chadboar/HEARTBEAT.md` | All bash commands use `boar` wrapper instead of `cd && python` |
| `~/.openclaw/agents/main/sessions/sessions.json` | Reset Telegram group session UUID |
| Cron job `4506c938...` | Delivery channel set to `telegram` |

## Remaining TODO
1. Verify end-to-end: user sends Telegram message -> bot replies in chat
2. If interactive delivery still fails, investigate gateway's inbound message -> delivery routing
3. Monitor for Grok HTML-encoding other characters beyond `&&`
4. Clean up test data from Telegram group session (subagent test messages)
