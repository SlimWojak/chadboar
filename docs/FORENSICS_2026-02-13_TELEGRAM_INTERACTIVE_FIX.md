# Forensics: Telegram Interactive Reply Fix — 2026-02-13

Session: Claude Opus 4.6 via Claude Code CLI + Grok 4.1 FAST intel from X/Reddit

## Summary

**Problem:** Bot received Telegram messages but never replied. Heartbeat cron worked,
CLI agent worked, outbound delivery worked — but inbound Telegram messages were
silently dropped. No errors logged.

**Root Cause:** Corrupted `lastUpdateId` in
`~/.openclaw/telegram/update-offset-default.json`. The stored offset was
**253,699,627** but actual Telegram update IDs were ~**7,243,884**. The gateway's
dedup check (`if (updateId <= lastUpdateId) return`) silently skipped every
incoming message.

**Fix:** Reset the offset file to `{"version":1,"lastUpdateId":null}`.

**Time to diagnose:** ~90 minutes of systematic elimination.

---

## Root Cause Chain

### Primary: Corrupted Telegram Update Offset (THE FIX)

File: `~/.openclaw/telegram/update-offset-default.json`

```json
// BEFORE (broken):
{"version":1,"lastUpdateId":253699627}

// AFTER (fixed):
{"version":1,"lastUpdateId":null}
```

The OpenClaw gateway persists the last-seen Telegram update ID to avoid
re-processing messages after restarts. The stored value (253M) was impossibly
higher than real Telegram update IDs (~7M), causing the gateway to skip ALL
incoming updates.

Source: `reply-B5GoyKpI.js` line 36625:
```javascript
if (lastUpdateId !== null && updateId <= lastUpdateId) return;
```

**How it got corrupted:** Unknown. Possibly from a previous bot token, a different
Telegram account, or a parsing error during an earlier session. The file was never
reset during any of the previous debugging sessions.

### Secondary Fixes Applied (important but not the blocker)

1. **`autoSelectFamily: true`** — Node.js 22 changed the default to `false`,
   breaking Telegram long-polling. Fixed in `channels.telegram.network`.

2. **Delivery context on Telegram group session** — The session's `deliveryContext`
   was missing the `to` field. Fixed by patching `sessions.json`:
   ```json
   "deliveryContext": {"channel": "telegram", "to": "-1003795988066"}
   ```

3. **Main session delivery context cleared** — Was incorrectly set to WhatsApp.
   Cleared to `{}`.

4. **`dmPolicy` changed from `"pairing"` to `"allowlist"`** — "pairing" requires
   an explicit pairing flow. "allowlist" allows DMs from users in `allowFrom`.

5. **`groupPolicy: "allowlist"` added** — Was missing entirely. Required for the
   gateway to route group messages to the agent.

6. **`groupAllowFrom` added** — Separate from per-group `allowFrom`. The global
   group policy checks this field.

---

## Diagnostic Trail

### What we checked (in order)

1. Bot token validity — `getMe` returned OK
2. Webhook conflicts — No webhook set, long-polling active
3. `autoSelectFamily` — Fixed, confirmed `true (config)` in logs
4. Suppression tokens — Already purged from prompts
5. Message tool banned for interactive replies — Already in AGENTS.md
6. Cron delivery channel — Already set to telegram
7. Session delivery context — Fixed (missing `to` field)
8. Main session WhatsApp delivery context — Cleared
9. `dmPolicy: "pairing"` vs `"allowlist"` — Changed
10. `allowFrom` string vs number format — Tested both, not the issue
11. `groupPolicy` missing — Added by `openclaw doctor --fix`
12. Telegram plugin vs channel conflict — Plugin required for polling
13. Open policies (all `"*"`) — Still failed, ruling out ALL filtering
14. **Verbose gateway mode** — Confirmed updates received but not processed
15. **Manual `getUpdates`** — Confirmed messages arrive correctly from Telegram
16. **Source code analysis** — Found `shouldSkipUpdate` → `lastUpdateId` check
17. **`update-offset-default.json`** — Found corrupted offset (253M vs 7M)
18. **Reset offset to null** — FIXED

### Key diagnostic commands

```bash
# Check the offset file (ROOT CAUSE)
cat ~/.openclaw/telegram/update-offset-default.json

# Reset it
echo '{"version":1,"lastUpdateId":null}' > ~/.openclaw/telegram/update-offset-default.json

# Run gateway in verbose mode to see raw updates
systemctl --user stop openclaw-gateway.service
OPENCLAW_LOG_LEVEL=debug timeout 30 openclaw gateway --port 18789 --verbose

# Manually poll Telegram (gateway must be stopped first)
curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates?limit=5&timeout=5"

# Check session delivery context
python3 -c "
import json
with open('$HOME/.openclaw/agents/main/sessions/sessions.json') as f:
    data = json.load(f)
for key, val in data.items():
    if 'telegram' in key:
        dc = val.get('deliveryContext', {})
        print(f'{key}: {dc}')
"
```

---

## Config Changes (openclaw.json)

| Field | Before | After |
|-------|--------|-------|
| `channels.telegram.dmPolicy` | `"pairing"` | `"allowlist"` |
| `channels.telegram.groupPolicy` | (missing) | `"allowlist"` |
| `channels.telegram.groupAllowFrom` | (missing) | `[915725856]` |
| `channels.telegram.streamMode` | (missing) | `"partial"` |
| `channels.telegram.network.autoSelectFamily` | (missing, default false) | `true` |

## Session Changes (sessions.json)

| Session | Before | After |
|---------|--------|-------|
| `agent:main:telegram:group:-1003795988066` deliveryContext | `{channel: "telegram"}` | `{channel: "telegram", to: "-1003795988066"}` |
| `agent:main:main` deliveryContext | `{channel: "whatsapp", to: "heartbeat"}` | `{}` |

---

## Patterns Harvested (for a8ra)

### 1. Silent Update Offset Corruption (NEW — CRITICAL)
OpenClaw persists `lastUpdateId` to `~/.openclaw/telegram/update-offset-default.json`.
If this value gets corrupted (set impossibly high), ALL inbound Telegram messages are
silently skipped with zero logging. No error, no warning, no "blocked" message.
This is invisible without verbose mode + source code inspection.

**Defense:** Monitor this file. If inbound messages stop working, reset it first.
Add this check to the session health guard.

### 2. Multiple Silent Failure Layers (ARCHITECTURE)
The Telegram pipeline has at least 5 silent failure points:
- `autoSelectFamily` (polling silently fails)
- `lastUpdateId` offset (updates silently skipped)
- `shouldSkipUpdate` dedup (messages silently dropped)
- `buildTelegramMessageContext` returns null (no agent turn, no log)
- `deliveryContext` missing target (response generated but not delivered)

Each layer fails silently. Debugging requires working from the outside in:
raw API → polling → update receipt → processing → agent turn → delivery.

### 3. `openclaw doctor` Doesn't Check Update Offset (GAP)
The doctor command checks config, sessions, and channel status but does NOT
check `update-offset-default.json` for sanity. This should be added.

### 4. Gateway Log File Permissions (MINOR)
The log file at `/tmp/openclaw/` can be owned by root if the gateway was
ever run as root. The user-mode gateway then silently fails to write to it.
Fix: `chown autistboar:autistboar /tmp/openclaw/openclaw-*.log`

---

## Verification

After fix:
- User sends message in Telegram group -> Bot replies in chat
- User sends DM to bot -> Bot replies in DM
- Cron heartbeat continues delivering to Telegram channel
- `update-offset-default.json` updates with correct IDs (~7M range)

## Status: RESOLVED
