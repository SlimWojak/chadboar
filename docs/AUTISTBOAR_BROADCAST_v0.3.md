# AutistBoar Broadcast v0.3 — CP-09 Session Report

**Date:** 2026-02-12
**Operator:** Opus (CTO session via Cursor Remote-SSH)
**Duration:** ~2 hours
**Commit:** `c11bea3` — `[CP-09] Fix heartbeat silence + structural migration to cron sessions`

---

## What Happened

Heartbeat went silent again after last session's restart. Boar was sending
heartbeats but Telegram received nothing. Root cause was deeper than session
collapse — it was a fundamental architectural flaw in how OpenClaw's native
heartbeat manages sessions.

### Root Cause Chain

1. **Native heartbeat reuses the same session across cycles.** Even with
   `session: "isolated"`, OpenClaw reuses the isolated session — it's isolated
   from `main`, not fresh per cycle.

2. **DeepSeek collapses after 1-2 successful cycles.** After generating one
   complete heartbeat response, DeepSeek sees the full context (prompt + tools +
   response) and pattern-matches to the shortest exit: `NO_REPLY` or
   `HEARTBEAT_OK`.

3. **`NO_REPLY` and `HEARTBEAT_OK` are gateway suppression tokens.** If either
   appears in the response, the gateway marks it `silent: true` and never
   delivers to Telegram. DeepSeek discovered `NO_REPLY` as an escape after
   the `message` tool failed with "Unknown target G".

4. **Session nuking is insufficient.** Deleting the `.jsonl` file while the
   gateway is running is useless — the gateway reconstructs from its in-memory
   cache. Even with the gateway stopped, the `sessionFile` field in
   `sessions.json` must be updated alongside the `sessionId`.

### The Fix: Cron with Isolated Sessions

Migrated heartbeat from the native scheduler to `openclaw cron`:

```
openclaw cron add --name "autistboar-heartbeat" --every 10m \
  --session isolated --model openrouter/deepseek/deepseek-chat \
  --timeout-seconds 300
```

Each cron run creates a **truly fresh session** (unique UUID per run). DeepSeek
never sees previous heartbeat context. Report delivery via the `message` tool
with explicit chat ID (`to: "915725856"`), bypassing the session-based Telegram
routing that broke with isolated sessions.

Native heartbeat disabled: `every: "0"` in `openclaw.json`.

**Verified:** 2 consecutive heartbeats (HB #6, #7) delivered to Telegram with
full checklist execution (guards, oracle, narrative, state updates).

---

## What Changed

### Config (outside repo)
- `~/.openclaw/openclaw.json`: Native heartbeat disabled (`every: "0"`)
- `openclaw cron`: New job `autistboar-heartbeat` (ID: `d6f61981`)
- `sessions.json`: New session ID (old sessions backed up)

### Code
- **AGENTS.md**: Updated Autonomous Mode section + comprehensive Heartbeat
  Operations lessons learned (v2 architecture, suppression tokens, session
  rotation protocol, diagnostic commands)
- **HEARTBEAT.md**: Added delivery rules (message tool with explicit chat ID),
  forbidden suppression tokens, duplicate-send prevention, rewrote alert
  instructions to use message tool instead of inline text
- **SOUL.md**: Added meta-purpose section (a8ra testbed framing)
- **lib/guards/zombie_gateway.py**: New guard using `/proc` filesystem instead
  of `pgrep` (fixes self-matching false positive where pgrep detects itself)
- **lib/guards/session_health.py**: New guard detecting session context collapse
  (consecutive short outputs in session history)
- **lib/state.py**: Added `dry_run_mode`, `dry_run_cycles_completed`,
  `dry_run_target_cycles` to Pydantic model (prevents silent data loss on save)
- **docs/BOAR_BRIEFING_CP09.md**: Telegram-ready briefing for Boar
- **docs/IDEA_BANK.md**: Full update with 19 items, a8ra-weighted tags

---

## Current State

- **Balance:** 14.0 SOL (~$1183 USD)
- **Positions:** 0 open
- **Mode:** Dry-run (7/10 cycles completed)
- **Heartbeat:** Cron-based, every 10m, isolated sessions ✅
- **Regime:** Yellow (no signals, markets quiet)
- **Guards:** Killswitch, drawdown, risk, zombie gateway, session health — all wired

---

## Car Park (Parked Items)

| Item | Status | Notes |
|------|--------|-------|
| Cron timeout | Monitor | First run timed out at 120s. Increased to 300s. Full execution takes 50-70s. |
| DeepSeek step-skipping | Known issue | Some runs skip guards/oracle. Isolated sessions help but don't guarantee full execution. |
| Stale session cleanup | Housekeeping | Each cron run leaves a `.jsonl` file. Will accumulate. Need periodic cleanup. |
| Interactive session routing | Unchanged | G's Telegram messages still use `main` session. Only heartbeat migrated to cron. |

---

## Patterns Harvested (for a8ra)

### 1. Session Accumulation Collapse (PROVEN)
Cheap models (DeepSeek) in accumulating sessions collapse to shortest-pattern
responses within 1-2 cycles. This will affect ANY agent running recurring loops
with session persistence. Fix: session isolation per cycle.

### 2. Gateway Suppression Tokens (DISCOVERED)
`NO_REPLY` and `HEARTBEAT_OK` are magic tokens that suppress Telegram delivery.
Any agent integration with messaging platforms needs to document and defend
against these silent-failure tokens.

### 3. Session State Is Not Just the File (DISCOVERED)
OpenClaw maintains session state in multiple places: `.jsonl` file, `sessions.json`
index (`sessionId` + `sessionFile`), and in-memory cache. Nuking only the file
is insufficient. Full rotation requires: stop gateway → delete file → update
index → start gateway.

### 4. pgrep Self-Matching (CLASSIC BUG)
`pgrep -f "pattern"` matches its own process command line. Use `/proc/PID/comm`
for reliable process detection. This is a known Linux gotcha but worth
documenting for agent-written guards.

### 5. Model-as-Scheduler vs Cron-as-Scheduler (ARCHITECTURE)
The native heartbeat treats the model as the execution engine within a persistent
session. The cron approach treats the model as a stateless function called per
cycle. For cheap models with limited instruction-following, the stateless
approach is strictly superior.

---

## What's Next

1. **Complete dry-run cycles** — 3 more cycles to reach 10/10, then assess
   readiness for live mode.
2. **Monitor cron stability** — Watch for timeout errors, step-skipping,
   message delivery reliability over 24 hours.
3. **Session file cleanup** — Either automated or manual periodic cleanup of
   accumulated `.jsonl` files from cron runs.
4. **Structured reasoning chains** (IDEA_BANK #8) — Next NOW-tagged item.
   Prototype auditable decision trees in heartbeat cycle.
5. **SkillRL distillation** (IDEA_BANK #10) — After beads accumulate. The
   learning flywheel.
