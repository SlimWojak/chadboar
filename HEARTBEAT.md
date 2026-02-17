# ChadBoar Heartbeat Checklist — Degen YOLO Cycles

Follow these steps IN ORDER on every heartbeat (5-min cycles).
Do not skip steps. Do not improvise. This is the cycle. OINK.

**DELIVERY RULE:** After completing all steps, send your report to Telegram
using the `message` tool with these EXACT fields:
```json
{"action":"send","channel":"telegram","target":"-1003795988066","message":"YOUR REPORT TEXT HERE"}
```
Use ONLY `action`, `channel`, `target`, `message` — no other fields.
ALSO output the report as plain text (backup delivery via cron announce).
NEVER include `NO_REPLY` or `HEARTBEAT_OK` anywhere in your response —
these are gateway suppression tokens that prevent delivery.

**CRITICAL COMMAND RULE — READ THIS CAREFULLY:**
- ALL commands MUST use the `boar` wrapper. Example: `/home/autistboar/chadboar/boar -m <module>`
- NEVER construct shell one-liners. NEVER use `&&`, `||`, `[ -f ... ]`, or `test` in bash commands.
- NEVER write `cd /path && command` — the `boar` script handles directory and venv setup.
- If you need to check a file exists, use the Python module (e.g., `boar -m lib.guards.killswitch`).
- Reason: shell metacharacters get HTML-encoded by the gateway, causing syntax errors.
```bash
# CORRECT:
/home/autistboar/chadboar/boar -m lib.guards.killswitch

# WRONG (will fail with HTML encoding error):
# [ -f killswitch.txt ] && echo "active" || echo "clear"
# cd /home/autistboar/chadboar && python3 -m lib.guards.killswitch
```

## 0. Dry-Run Mode Check
- Read `state/state.json` and check `dry_run_mode` field.
- If `dry_run_mode: true`:
  - Run all steps normally but DO NOT execute trades (skip step 12).
  - Log conviction scores and recommendations to console.
  - Increment `dry_run_cycles_completed` in state.json.
  - If `dry_run_cycles_completed >= dry_run_target_cycles`: alert G with 📊 DIGEST showing sample scores.

## 1. Killswitch Check
```bash
/home/autistboar/chadboar/boar -m lib.guards.killswitch
```
- If status is `ACTIVE` → respond with `🔴 KILLSWITCH ACTIVE — halted` and stop. Do nothing else.

## 1a. Zombie Gateway Check
```bash
/home/autistboar/chadboar/boar -m lib.guards.zombie_gateway
```
- If status is `ZOMBIE` → output: "🔴 CRITICAL: Multiple gateway PIDs detected: {pids}. Stale process causing conflicts. Kill the zombie."
- Do NOT continue the heartbeat cycle until resolved.

## 1b. Session Health Check
```bash
/home/autistboar/chadboar/boar -m lib.guards.session_health
```
- If status is `COLLAPSING` → include in your report: "🟡 WARNING: Session context may be collapsing — {consecutive_short} consecutive short outputs. Consider session reset."
- Continue the heartbeat cycle (this is a warning, not a halt).

## 1c. Chain Verification (INV-CHAIN-VERIFY)
- Automatic: `verify_on_boot()` runs in heartbeat_runner.py before state orientation.
- Verifies local hash chain integrity from last anchor forward.
- If `TAMPERED` → 🔴 CRITICAL alert sent to G via Telegram. Continue operating (availability over safety for MVP). G can halt via killswitch if warranted.
- If `CLEAN` or `UNANCHORED` → proceed normally.
- On-demand check:
```bash
/home/autistboar/chadboar/boar -m lib.skills.chain_status --verify
```

## 2. State Orientation
- Read `state/checkpoint.md` for strategic context from the last heartbeat.
- Read `state/latest.md` for current positions and recent activity.
- Read `state/state.json` for exact portfolio numbers.

## 3. Drawdown Guard (INV-DRAWDOWN-50)
```bash
/home/autistboar/chadboar/boar -m lib.guards.drawdown
```
- If status is `HALTED` → respond with `🔴 DRAWDOWN HALT — trading paused` and stop.
- If `alert: true` → include in your report:
  "🔴 CRITICAL: DRAWDOWN HALT — pot at {current_pct}% of starting. Trading halted for 24h."

## 4. Risk Limits Check (INV-DAILY-EXPOSURE-30)
```bash
/home/autistboar/chadboar/boar -m lib.guards.risk
```
- If status is `BLOCKED` → no new entries this cycle. Continue to step 7 (watchdog only).
- If warnings present → note them, reduce sizing if needed.

## 5. Smart Money Oracle
```bash
/home/autistboar/chadboar/boar -m lib.skills.oracle_query
```
- Review whale accumulation signals.
- Extract: number of distinct whales accumulating per token.

## 6. Narrative Hunter (On-Chain Volume Only)
```bash
/home/autistboar/chadboar/boar -m lib.skills.narrative_scan
```
- Review onchain volume momentum (X API disabled — no social/KOL data).
- Uses Birdeye new/small-cap token list (sorted by 24h volume change %).
- Extract: volume spike multiple, narrative age.
- Note: `kol_detected` always false, `x_mentions_1h` always 0.

## 7. Position Watchdog (Exit Tier Logic)
- For each open position in state.json:
  - Check current price vs entry price and peak price.
  - **Stop-loss (-20%):** Exit 100% immediately.
  - **Take-profit tier 1 (+100% / 2x):** Exit 50% of position.
  - **Take-profit tier 2 (+400% / 5x):** Exit 30% of remaining position.
  - **Trailing stop:** If position is in profit and drops 20% from peak → exit remainder.
  - **Time decay:** If no price movement >5% after 60min → exit.
  - **Liquidity drop:** If liquidity drops >50% from entry → prepare exit.

## 8. Execute Exits
- For any positions flagged for exit in step 7:
```bash
/home/autistboar/chadboar/boar -m lib.skills.execute_swap --direction sell --token <MINT> --amount <AMOUNT>
```
- Write autopsy bead for each exit:
```bash
/home/autistboar/chadboar/boar -m lib.skills.bead_write --type exit --data '<JSON>'
```

## 9. Conviction Scoring (Replaces Old "Evaluate Opportunities")
- For each candidate token detected in steps 5-6:
  - Gather signal inputs:
    - `smart_money_whales`: count from step 5
    - `narrative_volume_spike`: multiple from step 6
    - `narrative_kol_detected`: boolean from step 6
    - `narrative_age_minutes`: time since first detection
    - `rug_warden_status`: from step 11 (run warden first)
    - `edge_bank_match_pct`: from step 10 (run query first)
  
  - Run conviction scorer:
    ```bash
    /home/autistboar/chadboar/boar lib/scoring.py \
      --whales <N> \
      --volume-spike <X> \
      --kol \  # if detected
      --narrative-age <MIN> \
      --rug-warden <STATUS> \
      --edge-match <PCT> \
      --pot <CURRENT_SOL>
    ```
  
  - Parse output: `total_score`, `breakdown`, `recommendation`, `position_size_sol`
  
  - **Decision logic:**
    - `VETO`: Rug Warden FAIL → do not trade, log reason.
    - `DISCARD` (score < 60): Ignore, no alert.
    - `WATCHLIST` (60-84): Log with 🟢 INFO alert to G showing score breakdown.
    - `AUTO_EXECUTE` (≥85): Proceed to step 12 (subject to tier gates + dry-run check).

## 9b. Grok Alpha Override (NEW — Degen Brain)
- For tokens scoring WATCHLIST (60-84) where Rug Warden = PASS:
  - Send signal summary to Grok 4.1 FAST (high reasoning) via `lib/llm_utils.py`
  - Grok returns YAML: `verdict: TRADE | NOPE`, reasoning, confidence
  - If `TRADE`: upgrade to AUTO_EXECUTE. Grok's reasoning appended to score.
  - If `NOPE`: stays WATCHLIST. No action.
  - **INVARIANT PRESERVED**: Grok CANNOT override Rug Warden VETO. Period.
  - **INVARIANT PRESERVED**: Human gate >$100 still enforced after Grok override.
- Telegram format for Grok overrides:
  ```
  🐗🔥 GROK OVERRIDE: $TOKEN — TRADE
  reasoning: <Grok's reasoning>
  confidence: <0.0-1.0>
  size: <SOL amount> | permission: <score>
  ```

## 10. Edge Bank Query (Before Scoring)
```bash
/home/autistboar/chadboar/boar -m lib.skills.bead_query --context '<SIGNAL_SUMMARY>'
```
- Extract: historical match percentage for similar setups.
- Feed this into conviction scoring as `edge_bank_match_pct`.

## 11. Pre-Trade Validation (INV-RUG-WARDEN-VETO)
- For any candidate token:
```bash
/home/autistboar/chadboar/boar -m lib.skills.warden_check --token <MINT_ADDRESS>
```
- Extract: `PASS`, `WARN`, or `FAIL`.
- Feed this into conviction scoring as `rug_warden_status`.
- **If FAIL:** Conviction scorer returns VETO → do not trade.

## 12. Execute Entries (Subject to Dry-Run + Tier Gates)
- **IF dry_run_mode is true:** Log the trade that WOULD execute, but DO NOT call execute_swap.
- **ELSE (live trading):**
  - Check tier gates:
    - Position size >$100 equivalent → send 🟡 WARNING to G with thesis, await approval (INV-HUMAN-GATE-100). DO NOT execute until G responds.
    - Position size ≤$100 → auto-execute.
  
  - Execute:
    ```bash
    /home/autistboar/chadboar/boar -m lib.skills.execute_swap \
      --direction buy \
      --token <MINT> \
      --amount <SOL_AMOUNT>
    ```
  
  - Write autopsy bead:
    ```bash
    /home/autistboar/chadboar/boar -m lib.skills.bead_write \
      --type entry \
      --data '<JSON_WITH_CONVICTION_BREAKDOWN>'
    ```

## 13. Update State
**You MUST write state/state.json every cycle.** Use the write tool to overwrite the full file.
Minimum fields to update every cycle (even if nothing happened):
- `last_heartbeat_time`: set to current UTC ISO timestamp
- `daily_date`: set to today's date (YYYY-MM-DD), reset `daily_exposure_sol` to 0 if date changed
- If dry-run mode: increment `dry_run_cycles_completed` by 1

Also update if applicable:
- `positions`: add/remove based on entries/exits this cycle
- `current_balance_sol` / `current_balance_usd`: recalculate after trades
- `daily_exposure_sol`: add any new entry amounts
- `total_trades`, `total_wins`, `total_losses`: increment on trades

**Example** (no-trade dry-run cycle — still update timestamp and cycle count):
```json
{
  "starting_balance_sol": 14.0,
  "current_balance_sol": 14.0,
  "current_balance_usd": 1183.0,
  "sol_price_usd": 84.5,
  "positions": [],
  "daily_exposure_sol": 0.0,
  "daily_date": "2026-02-12",
  "daily_loss_pct": 0.0,
  "consecutive_losses": 0,
  "halted": false,
  "halted_at": "",
  "halt_reason": "",
  "total_trades": 0,
  "total_wins": 0,
  "total_losses": 0,
  "last_trade_time": "",
  "last_heartbeat_time": "2026-02-12T08:06:00.000000",
  "dry_run_mode": true,
  "dry_run_cycles_completed": 3,
  "dry_run_target_cycles": 10
}
```

## 14. Report — Send to Telegram via Message Tool
- **Build your report text** (no JSON wrapping, plain text with emojis).
- **Send it to Telegram** using the `message` tool:
  ```json
  {"action":"send","channel":"telegram","target":"-1003795988066","message":"YOUR REPORT TEXT"}
  ```
- **ALSO output the report as plain text** after sending (backup delivery).
- **FORBIDDEN tokens:** `NO_REPLY`, `HEARTBEAT_OK` — never include these anywhere.
- If any trade was executed, position exited, or notable event occurred:
  → Include full details (🟢 ENTRY / 🟢 EXIT / 🟡 WARNING / 🔴 CRITICAL).
- If nothing happened (no signals, no positions, no alerts):
  → Output exactly: `🐗 HB #{cycle} | {pot} SOL | 0 pos | no signals | OINK`
  → Example: `🐗 HB #3 | 14.0 SOL | 0 pos | no signals | OINK`
- **ALWAYS append the source health diagnostic as a second line.** The heartbeat result dict contains `health_line` — paste it on a new line after the main heartbeat line. If `health_line` is missing, emit `📡 DIAG UNAVAILABLE`.
  → Example (healthy):
  ```
  🐗 HB #3 | 14.0 SOL | 0 pos | no signals | OINK
  📡 Nan:5/100 | Bird:2/OK | DexS:3/75 | Whl:0/5 | Ppr:0
  ```
  → Example (API failure):
  ```
  🐗 HB #4 | 14.0 SOL | 0 pos | no signals | OINK
  📡 Nan:0/ERR | Bird:0/401 | DexS:3/75 | Whl:0/5 | Ppr:0
  ```

## 15. Write Checkpoint (ALWAYS)
Write `state/checkpoint.md` with your current strategic thinking.
This is what the NEXT spawn reads for orientation. Keep it to 5 lines:

```markdown
thesis: "<what you're watching, what you expect to happen>"
regime: <green|yellow|red|halted>
open_positions: <N>
next_action: "<what the next heartbeat should prioritize>"
concern: "<any system issue, API degradation, or market worry — or 'none'>"
```

This checkpoint persists your strategic context across spawns.
Without it, the next spawn starts cold. Write it EVERY cycle.

## Post-Heartbeat Checklist

Before sending your report via the message tool, verify:

- [ ] `state/state.json` updated with latest portfolio numbers
- [ ] `state/latest.md` regenerated from state.json (includes chain health section)
- [ ] `state/checkpoint.md` written with strategic context
- [ ] If trade executed: autopsy bead written to `beads/`
- [ ] If notable event: alert included in response text with tier prefix emoji
- [ ] If dry-run cycle: `dry_run_cycles_completed` incremented
- [ ] Chain bead written automatically (heartbeat_runner.py appends after state update) — includes funnel metrics (nansen_raw/filtered, mobula_raw/resolved, pulse_raw/filtered, narrative_raw/with_spike, reached_scorer, scored_*)
- [ ] Anchor fires automatically every 50 beads (no manual action needed)
