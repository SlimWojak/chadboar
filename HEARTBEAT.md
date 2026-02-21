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

## 5-12. UNIFIED PIPELINE (Oracle + Narrative + Warden + Scoring + Exits + Entries)

**Run the unified heartbeat pipeline as a SINGLE command:**
```bash
/home/autistboar/chadboar/boar -m lib.heartbeat_runner
```

This module executes ALL of the following in one call:
- Smart Money Oracle (Nansen TGM + Mobula Pulse + DexScreener fallback)
- Narrative Hunter (Birdeye volume anomaly scan)
- Position Watchdog (exit tier logic for open positions)
- Execute exits (stop-loss, take-profit, trailing stop, time decay)
- Rug Warden validation (graduation-aware thresholds for PumpFun tokens)
- Conviction Scoring (dual-profile: graduation vs accumulation plays)
- Grok Alpha Override (WATCHLIST → AUTO_EXECUTE upgrade)
- Execute entries (subject to dry-run + tier gates + human gate >$100)
- Write autopsy beads for entries/exits
- Update state/state.json with new positions/balances
- Append chain bead to flight recorder

**Parse the JSON output.** Key fields to report:
- `decisions[]`: list of trade decisions (AUTO_EXECUTE, WATCHLIST, PAPER_TRADE, VETO, DISCARD)
- `exits[]`: list of position exits (stop-loss, take-profit, trailing stop)
- `opportunities[]`: scored candidates with `permission_score`, `recommendation`, `play_type`
- `errors[]`: any API failures or issues during the cycle
- `health_line`: diagnostic string for the report footer
- `funnel`: pipeline metrics (nansen_raw, pulse_filtered, reached_scorer, scored_*)

**Decision logic (handled by the module — just report the outcomes):**
- `VETO`: Rug Warden FAIL → trade blocked. Report the reason.
- `DISCARD` (score < 25): Ignored, no alert.
- `PAPER_TRADE` (25-39): Phantom trade logged for calibration.
- `WATCHLIST` (40-49): Log with 🟢 INFO alert to G showing score breakdown.
- `AUTO_EXECUTE` (≥50 graduation, ≥75 accumulation): Trade executed (if not dry-run).
  - Position size >$100 → 🟡 WARNING sent, awaiting G approval (INV-HUMAN-GATE-100).
  - Position size ≤$100 → auto-executed.

**DO NOT run individual modules (oracle_query, narrative_scan, warden_check, scoring.py) separately.**
The unified runner handles play-type routing, graduation-aware thresholds, and proper signal merging.
Running modules individually will miss graduation warden thresholds and play-type detection.

## 13. Update State
**The heartbeat_runner (step 5-12) already updates state/state.json automatically.**
It updates: `last_heartbeat_time`, `daily_date`, positions, balances, trade counts, and dry-run cycle count.

After the runner completes, verify state/state.json was updated by checking `last_heartbeat_time`
matches the current cycle. If the runner failed or timed out, manually update at minimum:
- `last_heartbeat_time`: current UTC ISO timestamp
- `daily_date`: today's date (YYYY-MM-DD)

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
- [ ] `state/latest.md` auto-generated by heartbeat_runner (DO NOT overwrite — it contains accurate numbers from state.json. Read it for your report but NEVER rewrite it.)
- [ ] `state/checkpoint.md` written with strategic context
- [ ] If trade executed: autopsy bead written to `beads/`
- [ ] If notable event: alert included in response text with tier prefix emoji
- [ ] If dry-run cycle: `dry_run_cycles_completed` incremented
- [ ] Chain bead written automatically (heartbeat_runner.py appends after state update) — includes funnel metrics (nansen_raw/filtered, mobula_raw/resolved, pulse_raw/filtered, narrative_raw/with_spike, reached_scorer, scored_*)
- [ ] Anchor fires automatically every 50 beads (no manual action needed)
