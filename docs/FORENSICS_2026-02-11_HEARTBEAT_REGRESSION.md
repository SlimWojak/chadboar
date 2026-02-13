# Forensic Report: Heartbeat Regression (2026-02-11)

## Incident Summary

**Date:** 2026-02-11  
**Time:** 01:32 UTC - 03:48 UTC (2h 16m)  
**Severity:** Medium (system functional but degraded)  
**Impact:** Heartbeat cycles hitting wrong model, causing confusion responses

---

## Timeline

**01:32 UTC** — Commit `23166e7`: "Fix heartbeat reporting: add cron job"
- Created cron job `22bd4ed4-df98-4d11-a00d-e975f47808ed`
- Payload: Same as native heartbeat prompt
- sessionTarget: `main` (same as native heartbeat)
- **This was redundant** — native heartbeat already working

**01:32 - 03:36 UTC** — Silent period
- Both native heartbeat AND cron job running simultaneously
- Some cycles routed to DeepSeek (correct), some to Sonnet (incorrect)
- Sonnet cycles responded: "It seems the reminder content wasn't included..."

**03:36 UTC** — G reports regression
- Observed Sonnet confusion messages instead of proper heartbeat format
- Expected: `🟢 HB #N | 14.0 SOL | 0 pos | no signals | dry-run N/10`
- Actual: Confusion about "reminder content"

**03:37 - 03:48 UTC** — Forensic investigation
- Claude (Sonnet) runs full investigation
- Reviews commit history, cron jobs, OpenClaw config
- Identifies dual-triggering issue

**03:48 UTC** — Fix deployed
- Deleted cron job `22bd4ed4-df98-4d11-a00d-e975f47808ed`
- Updated 4 documentation files with warnings
- Committed forensic notes

---

## Root Cause

**Confusion about OpenClaw heartbeat architecture.**

**Correct pattern:**
- Native heartbeat configured in `~/.openclaw/openclaw.json`
- Triggers every 10 minutes automatically
- Routes to `openrouter/deepseek/deepseek-chat`
- Delivers to Telegram via `target: "telegram"`

**Incorrect pattern (what was done):**
- Created manual cron job with identical prompt
- Cron job also triggered every 10 minutes
- Cron job hit `main` session → routed to Sonnet (chat model)
- Sonnet unfamiliar with HEARTBEAT.md → confusion responses

**Why it happened:**
- AGENTS.md said "triggered by Gateway heartbeat" (vague)
- Misinterpreted as needing manual cron job setup
- Didn't check existing `openclaw.json` config first

---

## Technical Details

### Native Heartbeat Config (Correct)
```json
// ~/.openclaw/openclaw.json
{
  "agents": {
    "defaults": {
      "heartbeat": {
        "every": "10m",
        "model": "openrouter/deepseek/deepseek-chat",
        "session": "main",
        "target": "telegram",
        "prompt": "Read HEARTBEAT.md if it exists (workspace context). Follow it strictly. Do not infer or repeat old tasks from prior chats. If nothing needs attention, reply HEARTBEAT_OK."
      }
    }
  }
}
```

### Redundant Cron Job (Incorrect)
```json
{
  "id": "22bd4ed4-df98-4d11-a00d-e975f47808ed",
  "name": "AutistBoar 10-min Heartbeat",
  "schedule": { "kind": "every", "everyMs": 600000 },
  "sessionTarget": "main",
  "payload": {
    "kind": "systemEvent",
    "text": "Read HEARTBEAT.md if it exists (workspace context). Follow it strictly. Do not infer or repeat old tasks from prior chats. If nothing needs attention, reply HEARTBEAT_OK."
  }
}
```

**Problem:** Both systems injecting same prompt into `main` session → race condition on which model handles it.

---

## Impact Assessment

**Affected Heartbeats:** ~12 cycles (01:32 - 03:48 UTC)  
**System Safety:** ✅ No impact (dry-run mode active, no trades)  
**Data Integrity:** ✅ State files intact  
**Cost Impact:** ~$0.30 extra (Sonnet cycles at 10x DeepSeek cost)  
**User Experience:** ❌ Confusing "reminder not found" messages to G

---

## Fix Applied

1. **Deleted cron job** `22bd4ed4-df98-4d11-a00d-e975f47808ed`
2. **Updated documentation:**
   - `AGENTS.md`: Clarified "OpenClaw native heartbeat" + warning against cron
   - `docs/ROUTING.md`: Added "⚠️ Do NOT Use Cron for Heartbeats" section
   - `BOOTSTRAP.md`: Added "Heartbeat Architecture" section with flow diagram
   - `docs/ORIENTATION_HABITS.md`: Added critical system knowledge section
3. **Updated conversation context** with decision log
4. **Created this forensic report** for future reference

---

## Prevention Measures

**For Future Claude Spawns:**

1. **Boot sequence now includes heartbeat architecture reminder**
   - BOOTSTRAP.md section explains native vs cron pattern
   - ORIENTATION_HABITS.md reinforces "NEVER use cron for heartbeats"

2. **Documentation clarity improved**
   - AGENTS.md now says "OpenClaw native heartbeat (configured in openclaw.json)"
   - ROUTING.md has prominent warning section

3. **Diagnostic checklist added to ROUTING.md**
   - If heartbeats seem broken → check `openclaw.json` first
   - Do NOT create cron job as a fix

**For System Monitoring:**

- Native heartbeat continues automatically via OpenClaw
- Next heartbeat cycle (~03:58 UTC) should route to DeepSeek correctly
- Expected output: `🟢 HB #9 | 14.0 SOL | 0 pos | no signals | dry-run 9/10`

---

## Lessons Learned

1. **Check existing infrastructure before adding new systems**
   - Native heartbeat was already configured and working
   - Should have verified `openclaw.json` first

2. **Cron jobs are NOT for heartbeats**
   - Cron jobs are for reminders, wake events, one-off tasks
   - Native heartbeat system handles periodic agent cycles

3. **Model routing matters**
   - Native heartbeat → DeepSeek (cheap, structured)
   - Cron systemEvent → Sonnet (expensive, conversational)
   - Mixing these causes confusion and cost increase

4. **Documentation precision prevents errors**
   - Vague terms like "Gateway heartbeat" can mislead
   - Explicit references to config files help future spawns

---

## Verification Steps

**Next heartbeat cycle (~03:58 UTC):**
1. Confirm DeepSeek model handles it (not Sonnet)
2. Verify proper format: `🟢 HB #9 | 14.0 SOL | 0 pos | no signals | dry-run 9/10`
3. No "reminder content not found" errors
4. Cron job list should remain empty

**Monitoring command:**
```bash
# Verify no cron jobs exist
openclaw cron list

# Check native heartbeat config
cat ~/.openclaw/openclaw.json | jq '.agents.defaults.heartbeat'
```

---

## Status: RESOLVED ✅

**Fix deployed:** 2026-02-11 03:48 UTC  
**Confidence:** High (cron job deleted, docs updated, native heartbeat verified)  
**Next verification:** ~03:58 UTC (next heartbeat cycle)
