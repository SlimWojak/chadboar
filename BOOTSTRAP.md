# ChadBoar — Bootstrap Procedure

This file defines how ChadBoar initializes on fresh deployment or after a hard reset.

## Initial Deployment

1. **Read BOAR_MANIFEST.md** — get your bearings
2. **Check for state/state.json** — if missing, you're uninitialized
3. **Wait for G's `/start` command** — do not auto-initialize
4. **When G provides starting balance:**
   - Fetch current SOL price from CoinGecko
   - Calculate starting_balance_sol = starting_usd / sol_price
   - Write state/state.json with initial values
   - Write state/latest.md summary
   - Update state/checkpoint.md with "initialized" status
5. **Confirm to G:** "Initialized with X SOL ($Y USD). Ready for first heartbeat."

## Normal Boot (state exists)

**Follow ORIENTATION_HABITS.md 5-file boot sequence:**

1. Read BOAR_MANIFEST.md — system map, invariants, file locations
2. Read state/checkpoint.md — strategic context from last heartbeat
3. Read state/conversation_context.md (tail -20) — recent decisions and current topic
4. Check git log -3 --oneline — what changed recently
5. Read state/state.json — current numbers (balance, positions, dry-run progress)

**Then determine mode:**
6. If triggered by heartbeat → follow HEARTBEAT.md strictly
7. If triggered by Telegram → respond as the scout persona

**Boot time:** < 2 seconds for full orientation

---

## Heartbeat Architecture (Critical Understanding)

**OpenClaw Native Heartbeat System:**
- Configured in `~/.openclaw/openclaw.json` under `agents.defaults.heartbeat`
- Triggers every 10 minutes automatically
- Routes to Grok 4.1 FAST model (high reasoning)
- Injects prompt: "Read HEARTBEAT.md if it exists..."
- Delivers output to Telegram when configured

**NEVER create cron jobs for heartbeats.** The native system handles this.

**Cron jobs are ONLY for:**
- Scheduled reminders (e.g., "remind me in 24h")
- Wake events
- One-off tasks

**If you think heartbeats aren't working:**
1. Check `openclaw.json` config: `cat ~/.openclaw/openclaw.json | jq '.agents.defaults.heartbeat'`
2. Verify model is `openrouter/x-ai/grok-4-fast`
3. Check `every: "10m"` and `session: "main"`
4. **Do NOT create a cron job** — this causes model selection conflicts

**Heartbeat Flow:**
```
Cron heartbeat (5min) → Grok reads HEARTBEAT.md → Executes cycle → Reports to Telegram
```

**What happens if you create a cron job by mistake:**
- Both native heartbeat AND cron fire every 10 minutes
- Cron hits wrong model instead of Grok
- Sonnet gets confused (no HEARTBEAT.md context loaded)
- Costs 10x more per cycle
- Creates "reminder content not found" errors

## Hard Reset (wipe all state)

Only execute when G explicitly requests it with `/reset` or similar clear command.

1. Move state/state.json to state/archive/state_YYYY-MM-DD_HH-MM-SS.json
2. Move all beads/*.md to beads/archive/
3. Reset state/checkpoint.md to template defaults
4. Reset state/latest.md to template defaults
5. Await new `/start` command from G

## Environment Checks

Before first heartbeat, verify:
- Python 3.11+ installed
- All lib/ dependencies installed (requirements.txt)
- .env file exists with API keys for: HELIUS_API_KEY, BIRDEYE_API_KEY, NANSEN_API_KEY, X_API_BEARER_TOKEN
- skills/ directory contains all 5 skill SKILL.md files
- lib/guards/ contains killswitch.py, drawdown.py, risk.py
- lib/signer/ exists (even if stub for now)

If any check fails, report to G immediately.

## Security Invariants at Boot

- Confirm killswitch.txt does NOT exist (unless G set it)
- Confirm private key is NOT in any .env, config, or plaintext file in workspace
- Confirm all invariants from AGENTS.md are encoded in lib/guards/

## First Heartbeat Expectations

The first heartbeat will likely:
- Find no signals (no historical context yet)
- Build initial API connections
- Populate first bead query index (empty results expected)
- Write first real checkpoint.md with live market regime assessment

Do not trade on the first heartbeat unless signal convergence is extraordinary.
Bias toward observation on boot.
