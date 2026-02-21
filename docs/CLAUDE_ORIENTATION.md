# ChadBoar: Claude Orientation & Regression Prevention

Dense machine-to-machine reference. Read this FIRST before modifying any code.

## Architecture Overview

ChadBoar is a Solana memecoin trading bot. OpenClaw gateway (Node.js) runs a Grok agent on a 10-minute cron. Grok reads `HEARTBEAT.md`, invokes Python via the `boar` wrapper, reports to Telegram. All Python execution goes through `/home/autistboar/chadboar/boar -m <module>` which handles cd, venv activation, and .env sourcing.

```
OpenClaw Gateway (Node.js, systemd user service)
  -> Cron (*/10 * * * *)
    -> Grok agent (xai/grok-4-1-fast)
      -> reads HEARTBEAT.md (14-step checklist)
      -> invokes: /home/autistboar/chadboar/boar -m lib.heartbeat_runner
      -> sends report to Telegram via message tool
```

## Directory Map

```
config/risk.yaml          # Thresholds, circuit breakers, exit tiers, sizing
config/firehose.yaml      # API endpoints, rate limits, RPC fallback chain
lib/heartbeat_runner.py   # MAIN ORCHESTRATOR — 6 stages, ~1600 lines
lib/scoring.py            # ConvictionScorer, SignalInput, play type detection
lib/skills/execute_swap.py    # Jupiter quote -> sign -> Jito/RPC -> confirm
lib/skills/paper_trade.py     # Phantom trade logging + 6h expiry + PnL tracking
lib/skills/oracle_query.py    # Nansen TGM + Mobula Pulse signal aggregation
lib/skills/pulse_quick_scan.py # Pulse-sourced graduation plays + scalp exits
lib/skills/warden_check.py    # 6-point Rug Warden validation
lib/signer/keychain.py        # Parent-side: sign_transaction(), verify_isolation()
lib/signer/signer.py          # Subprocess: reads STDIN unsigned tx, writes STDOUT signed tx
lib/beads/schema.py            # 10 bead types (FACT/CLAIM/SIGNAL/PROPOSAL/etc)
lib/beads/chain.py             # SQLite hash chain (WAL mode, Merkle batching)
lib/beads/emitters.py          # emit_* functions for all bead types
lib/clients/birdeye.py         # Price/liquidity/holders/volume/security
lib/clients/jupiter.py         # Swap quotes + unsigned tx generation
lib/clients/jito.py            # MEV-protected bundle submission
lib/clients/nansen.py          # Smart money TGM suite
lib/clients/helius.py          # Solana RPC + token metadata
lib/guards/killswitch.py       # killswitch.txt exists -> halt all
lib/guards/drawdown.py         # pot < 50% starting -> halt 24h
lib/guards/risk.py             # daily exposure > 30% -> block new entries
state/state.json               # CANONICAL: positions, balance, exposure, halt status
state/paper_trades.json        # Phantom trades (closed field, not status field)
state/beads.db                 # SQLite bead chain database
```

## Heartbeat Pipeline (6 Stages)

Entry: `run_heartbeat()` in `lib/heartbeat_runner.py`, timeout 120s.

### Stage 0: Init Context
Emit POLICY + MODEL_VERSION beads on config change. Record cycle metadata.

### Stage 1: Watchdog (`stage_watchdog` -> `run_position_watchdog`)
For each position in state.json:
- Fetch price from Birdeye (`batch_price_fetch`, max 3 concurrent)
- Compute PnL using **market cap** (NOT per-token price — see CRITICAL BUG below)
- Generate exit decisions based on mcap-aware tier (see Exit Tiers)
- If Birdeye fails for a position: generate "Price fetch failed" exit at 100%

### Stage 1b: Execute Exits (`stage_execute_exits`)
For each exit decision:
- Get wallet pubkey via `get_wallet_pubkey()` (requires SIGNER_KEY_PATH)
- Call `execute_swap(direction="sell", ...)` with on-chain confirmation
- Update state atomically (remove or reduce position)
- On failure: log error, position stays in state

### Stage 2: Oracle (`stage_oracle`)
- Nansen TGM: `/smart-money/dex-trades` -> filter wallet_count >= 1
- Mobula Pulse: bonding/bonded tokens with holder categorization
- Merge by mint, dedup

### Stage 3: Narrative (`stage_narrative`)
- DexScreener candidates (primary) or Birdeye fallback
- Filter: volume_vs_avg >= 2x spike

### Stage 4: Score & Execute (`stage_score_and_execute`)
For each candidate mint:
1. **Volume gate**: skip if < $5k volume
2. **Play type detection**: graduation (Pulse + no whales) vs accumulation
3. **Rug Warden**: 6-point check, play-type-aware thresholds
4. **Conviction scoring**: weight profile by play type
5. **Decision**: VETO / DISCARD / PAPER_TRADE / WATCHLIST / AUTO_EXECUTE
6. **Execution**: Jupiter quote -> sign -> Helius RPC (3 retries) -> confirm on-chain -> update state

### Stage 5: Finalize
Paper trade PnL checks + 6h expiry, heartbeat bead, Merkle anchor (every 50 beads), health_line.

## Scoring System

### Play Types
| Type | Detection | Auto-Execute | Max Position | Daily Limit |
|------|-----------|-------------|--------------|-------------|
| graduation | Pulse signals + no whales | >= 60 | $30 | 8/day |
| accumulation | Whale signals or mixed | >= 75 | 5% of pot | none |

### Weight Profiles
**Graduation**: pulse_quality=35, narrative=30, rug_warden=25, edge_bank=10
**Accumulation**: smart_money=40, narrative=30, rug_warden=20, edge_bank=10

### VETO Conditions (absolute blockers)
1. Rug Warden FAIL
2. Token age < 2min AND volume spike >= 5x
3. **Serial deployer**: `pulse_deployer_migrations > 5` (data: -26% avg PnL)
4. Graduation daily sublimit exceeded (`daily_graduation_count >= max_daily_plays`)
5. All whales are dumpers
6. **Graduation mcap too high**: `entry_market_cap_usd > max_mcap_graduation` ($500k default — not a micro-cap speed play)

### Red Flag Penalties (permission_score deductions)
- Concentrated volume: -15
- Dumper wallets: -15 (one) or -30 (multiple)
- Fresh wallet inflow > $50k: -10
- Exchange inflow: -10
- S2 divergence (whales but no volume): -25
- Pulse low organic < 0.3: -10
- Pulse bundlers > 20%: -10
- Pulse snipers > 30%: -10
- **FDV death zone** (graduation $25k-$100k): -15 (data: -40% avg PnL)
- **Post-bonding trap** (pulse_stage == "bonded"): -10 (data: -45.74% avg PnL)

### Permission Gate (A1)
Graduation: 1+ primary source. Accumulation: 2+ primary sources.
Primary = oracle (whales>=1), narrative (vol>=3x), warden (PASS), pulse (pro>10% AND organic>=0.3).

## Exit Tier System

`_get_mcap_exit_tier()` in heartbeat_runner.py:1510. Driven by entry market cap.

| Entry mcap | TP1 PnL | TP1 Sell | TP2 PnL | TP2 Sell | Trail | Decay | SL |
|-----------|---------|----------|---------|----------|-------|-------|----|
| < $100k | +80% | 40% | +200% | 40% | 25% | 20min | -30% |
| < $500k | +60% | 50% | +150% | 30% | 20% | 30min | -25% |
| < $2M | +40% | 50% | +100% | 30% | 15% | 45min | -20% |
| >= $2M | +30% | 50% | +60% | 30% | 12% | 60min | -15% |

Graduation: `decay_min = max(15, tier_decay_min // 2)`.

Time decay fires when: `age >= decay_min AND abs(pnl_pct) < 5%`.

## Execute Swap Flow

`lib/skills/execute_swap.py` — Jupiter + Helius RPC with Blind KeyMan signing.

```
1. Jupiter quote (buy or sell)
2. If dry_run: return DRY_RUN status, skip rest
3. verify_isolation() — ensure SIGNER_PRIVATE_KEY not in agent env
4. Jupiter get_swap_transaction(prioritizationFeeLamports="auto") -> unsigned tx (base64)
5. sign_transaction() -> isolated subprocess signs -> signed tx (base64)
6. Submit to Helius RPC (skipPreflight=True, 3 retries with 2s gaps for leader rotation)
7. Confirm on-chain: poll getSignatureStatuses every 4s, up to 32s (searchTransactionHistory=True)
   - If confirmed with no error: return SUCCESS
   - If confirmed with error: return FAILED
   - If not confirmed after 32s: return FAILED
```

**NOTE**: Jito bundles are NOT used (require a tip transaction we don't build). `lib/clients/jito.py` exists but is unused. Go direct to Helius RPC.

## Signer Isolation (INV-BLIND-KEY)

Private key NEVER enters agent process memory. Flow:
1. `keychain.py` reads key from file at SIGNER_KEY_PATH
2. Spawns subprocess `lib/signer/signer.py` with minimal env (only SIGNER_PRIVATE_KEY, PATH, PYTHONPATH)
3. Subprocess: decode base64 key -> solders.Keypair -> `VersionedTransaction(msg, [keypair])` -> stdout signed tx
4. Parent reads stdout, clears key reference

Key file: `/home/autistboar/.config/autistboar/signer.key` (chmod 400, base64 Ed25519)
Wallet: `B1rUxZxotGKLaoaLcs3evctfJFP6YvywRwCaech8wjRo`

## State File (state/state.json)

```json
{
  "starting_balance_sol": 14.0,
  "current_balance_sol": 14.0,      // MUST match on-chain SOL balance
  "positions": [{                    // MUST match on-chain token balances
    "token_mint": "...",
    "entry_amount_sol": 0.5,
    "entry_amount_tokens": 1000000,
    "entry_market_cap_usd": 10000,   // REQUIRED for PnL calc
    "play_type": "graduation",
    "tier1_exited": false,
    "tier2_exited": false
  }],
  "daily_exposure_sol": 0,           // Resets when daily_date changes
  "daily_date": "2026-02-21",
  "daily_graduation_count": 0,       // Resets daily, blocks at max_daily_plays
  "dry_run_mode": false,
  "halted": false
}
```

## Paper Trade System

File: `state/paper_trades.json`. Uses `closed` field (boolean), NOT `status`.

- Logged for scores 25-49 (PAPER_TRADE + WATCHLIST recommendations)
- PnL checked each cycle for 10 most recent open trades
- Closed at 6h age (`close_reason: "6h_expiry"`)
- Autopsy beads emitted for closed trades
- Rate-limited: 15 expiry price fetches + 10 PnL checks per cycle

## Bead Types

10 types in SQLite (`state/beads.db`). Each has: bead_id (UUID v7), hash chain, ECDSA attestation, bi-temporal timestamps.

| Type | Purpose |
|------|---------|
| FACT | Market data observation (price, volume, holders) |
| CLAIM | Logical assertion with provenance |
| SIGNAL | Scored trading opportunity with conviction score |
| PROPOSAL | Trade action (ENTER_LONG/EXIT_LONG) with tx_signature |
| PROPOSAL_REJECTED | Veto/failure reason with counterfactual |
| AUTOPSY | Post-trade evaluation (pnl, lesson, supports_thesis) |
| HEARTBEAT | Cycle metadata (funnel metrics, stage results) |
| POLICY | Rule enforcement record |
| MODEL_VERSION | LLM deployment record |
| SKILL | Capability record |

## Critical Environment Variables

```
SIGNER_KEY_PATH=/home/autistboar/.config/autistboar/signer.key
HELIUS_API_KEY=...          # Solana RPC (primary)
BIRDEYE_API_KEY=...         # Price/liquidity in watchdog + warden
NANSEN_API_KEY=...          # Whale signals in oracle
TELEGRAM_BOT_TOKEN=...      # Heartbeat delivery
TELEGRAM_CHANNEL_ID=-1003795988066
```

All loaded via `.env` file, sourced by `boar` wrapper with `set -a`.

## Risk Guards

| Guard | Invariant | Threshold | Action |
|-------|-----------|-----------|--------|
| killswitch.py | INV-KILLSWITCH | killswitch.txt exists | halt all |
| drawdown.py | INV-DRAWDOWN-50 | pot < 50% starting | halt 24h |
| risk.py | INV-DAILY-EXPOSURE-30 | exposure > 30% of pot | block new entries |
| risk.py | Circuit breaker | 3 consecutive losses | reduce size 50% |
| risk.py | Daily loss | > 10% daily loss | halt rest of day |
| session_health.py | Hallucination | stale canary | warn, continue |
| zombie_gateway.py | Stale PID | multiple PIDs | halt until resolved |

---

## CRITICAL BUGS FIXED — DO NOT REGRESS

### 1. Unit Mismatch in PnL Calculation
**Files**: heartbeat_runner.py:1570-1578, pulse_quick_scan.py
**Bug**: Jupiter `entry_price` is USD/smallest-token-unit. Birdeye `price` is USD/whole-token. For 6-decimal PumpFun tokens this is a 10^6 multiplier -> phantom millions % PnL.
**Fix**: Use `entry_market_cap_usd` vs `current_mc` for PnL. NEVER compare entry_price to Birdeye price directly.
```python
if entry_mc > 0 and current_mc > 0:
    pnl_pct = ((current_mc - entry_mc) / entry_mc) * 100
else:
    pnl_pct = 0.0  # Skip — unit mismatch makes price-based PnL unreliable
```
**Regression test**: If pnl_pct > 10000% on a micro-cap, something is wrong.

### 2. Phantom Positions from Unconfirmed Transactions
**File**: execute_swap.py:154-200
**Bug**: `execute_swap` returned SUCCESS after tx submission without waiting for on-chain confirmation. Transactions silently dropped -> state.json had positions with no on-chain tokens.
**Fix**: Poll `getSignatureStatuses` for up to 30s. Only return SUCCESS when confirmed on-chain with no errors.
**Regression test**: After any buy, verify `getTokenAccountsByOwner` shows the expected token balance. If `positions[]` is non-empty but wallet has 0 token accounts, positions are phantom.

### 3. Dead Exit Execution
**File**: heartbeat_runner.py:301-411 (stage_execute_exits)
**Bug**: `run_position_watchdog()` generated exit decisions but nothing consumed them.
**Fix**: `stage_execute_exits()` wired into pipeline after watchdog (line 1464-1468). Handles partial sells (tier exits) and full sells.
**Regression test**: If positions persist beyond time_decay limit with 0% PnL, exits are broken.

### 4. entry_price_fdv=0 on Paper Trades
**File**: heartbeat_runner.py:905-912 (PAPER_TRADE path)
**Bug**: Nansen dex-trades signals lack `token_bought_market_cap` for fresh tokens -> market_cap=0 -> paper trade entry_price_fdv=0 -> all PnL checks show 0%.
**Fix**: Birdeye FDV fallback: if entry_fdv==0, fetch from `birdeye.get_token_overview(mint)`.
**Regression test**: Check `paper_trades.json` for `entry_price_fdv: 0` entries. Should be rare (only if Birdeye also fails).

### 5. Balance Corruption
**Bug**: Phantom PnL from unit mismatch (bug #1) inflated `current_balance_sol` from 14 to 9.3M.
**Fix**: Market-cap PnL (bug #1) + prefer actual sell proceeds from `execute_swap.amount_out`.
**Regression test**: `current_balance_sol` should never exceed `starting_balance_sol` by more than 10x without many confirmed profitable trades.

### 6. Scoring: Serial Deployer was Penalty, Now VETO
**File**: scoring.py, VETO section
**Bug**: Serial deployers (>5 migrations) got only -10pt penalty. Data showed -26% avg PnL.
**Fix**: Hard VETO before any positive scoring. Old -10 penalty code removed.

### 7. Scoring: FDV Death Zone
**File**: scoring.py, red flags section
**Bug**: Graduation plays in $25k-$100k mcap range had -33% to -48% avg PnL with no penalty.
**Fix**: -15pt red flag for graduation AND $25k < mcap < $100k.

### 8. Scoring: Post-Bonding Trap
**File**: scoring.py, red flags + score_pulse_quality
**Bug**: `pulse_stage == "bonded"` got +5 bonus. Data showed -45.74% avg PnL.
**Fix**: Removed bonus for bonded. Added -10pt red flag. Only `bonding` (pre-graduation) gets the +5 bonus.

### 9. Volume Gate
**File**: heartbeat_runner.py:765-771
**Bug**: 39% of trades were on <$5k volume tokens with 5% win rate. Pure noise in bead stream.
**Fix**: Skip tokens with volume_usd < $5000 before scoring loop.

### 10. Orphaned Signals
**File**: heartbeat_runner.py (dry-run and failed execution paths)
**Bug**: Dry-run mode and failed executions didn't emit proposal/rejected beads -> 713 SIGNAL beads with no corresponding PROPOSAL.
**Fix**: Dry-run trades emit PROPOSAL with gate="dry_run". Failed trades emit PROPOSAL_REJECTED with rejection_source="execution".

### 11. Signer SignatureFailure (every tx dropped from mempool)
**File**: lib/signer/signer.py:65-78
**Bug**: `keypair.sign_message(bytes(msg))` + `VersionedTransaction.populate(msg, [sig])` produces **invalid signatures** for Solana VersionedTransactions. The `sign_message()` method signs raw bytes but Solana validators expect a domain-separated hash with versioned message prefix. Every transaction was accepted by the RPC but silently dropped by validators.
**Fix**: Use `VersionedTransaction(tx.message, [keypair])` constructor which handles the signing protocol correctly.
```python
# WRONG — invalid signature, tx dropped:
signature = keypair.sign_message(bytes(msg))
signed_tx = VersionedTransaction.populate(msg, [signature])

# CORRECT — valid signature, tx lands:
signed_tx = VersionedTransaction(tx.message, [keypair])
```
**Regression test**: After signing, simulate with `sigVerify: True`. If `err: SignatureFailure`, the signer is broken. A valid signature produces `err: None` with non-zero `unitsConsumed`.

### 12. State Overwrite by stage_finalize (positions vanish after each heartbeat)
**File**: lib/heartbeat_runner.py:1283-1292 (stage_finalize)
**Bug**: `run_heartbeat()` loads `state` at line 1397. This stale dict is passed to all stages. `stage_score_and_execute` correctly re-reads state from disk at line 1211 and writes positions at line 1221. BUT `stage_finalize` receives the original stale `state` (without positions) and writes it back at line 1292, **overwriting every position**. This is why positions appeared as phantom — they existed on disk for milliseconds before finalize clobbered them.
**Fix**: `stage_finalize` re-reads `state = safe_read_json(state_path)` before applying its updates.
**Regression test**: After a heartbeat with `trades_attempted > 0`, check state.json immediately. If positions is empty but on-chain tokens exist, finalize is overwriting state.

### 13. Dead Jito Path (15s wasted timeout on every trade)
**File**: lib/skills/execute_swap.py (old Step 4)
**Bug**: Jito `send_bundle()` requires a tip transaction (SOL transfer to a Jito tip account) in the bundle. We never included one, so Jito always rejected the bundle. The 15s timeout expired, then we fell back to bare RPC `sendTransaction`. Combined with the signer bug (#11), this meant every trade: Jito timeout (15s) -> RPC submit with bad sig -> dropped.
**Fix**: Removed Jito from the execution path entirely. Go direct to Helius RPC with `skipPreflight=True`, 3 retries with 2s gaps (handles Solana leader rotation), Jupiter auto priority fees (`prioritizationFeeLamports: "auto"`). If Jito is needed in the future, must implement tip transaction construction.
**Regression test**: Trade execution should complete in <10s (quote + sign + submit + confirm). If taking >30s, something is adding unnecessary latency.

### 14. SL Exit Infinite Retry Loop (Custom 6024 slippage failure)
**File**: lib/heartbeat_runner.py (stage_execute_exits)
**Bug**: `stage_execute_exits` used fixed `slippage_bps=500` (5%) for ALL exits. Micro-cap tokens that trigger stop-loss have cratered in price and liquidity. Selling at 5% max slippage causes Jupiter Custom 6024 (ExceededSlippageTolerance). The bot logged the failure and moved on. Next heartbeat: same SL detection, same 5% attempt, same failure. Position bled from -30% to -84% over multiple cycles with no exit.
**Fix**: Escalating slippage for critical/high urgency exits: 500 → 1500 → 4900 bps. Only escalates on Custom 6024 errors. Normal exits (TP, decay) stay at 500 bps.
**Regression test**: If a position persists past its SL threshold for more than 2 heartbeats, escalation is broken. Check exit_execution stage health for `exits_failed > 0` on the same mint across cycles.

### 15. Exit Win/Loss Counters Never Updated
**File**: lib/heartbeat_runner.py (stage_execute_exits)
**Bug**: `stage_execute_exits` incremented `total_trades` but never `total_wins`, `total_losses`, or `consecutive_losses`. Only `pulse_quick_scan.py` had this logic. The circuit breaker (3 consecutive losses = 50% size reduction) was blind to all watchdog exits. Could accumulate unlimited losses without triggering risk reduction.
**Fix**: After each successful exit, compute PnL from `sol_received vs sol_portion`. Update `total_wins`/`total_losses`, reset/increment `consecutive_losses`, track `daily_loss_pct`.
**Regression test**: After any exit via watchdog, check state.json. `total_wins + total_losses` should increase by 1. If `total_wins == 0 AND total_losses == 0` after confirmed exits, tracking is broken.

### 16. Full Exit Deletes All Duplicate-Mint Positions
**File**: lib/heartbeat_runner.py (stage_execute_exits)
**Bug**: Full exit filter `state["positions"] = [p for p in ... if p["token_mint"] != mint]` removes ALL entries for that mint. But the sell only covers the first match's `entry_amount_tokens`. For XMN x3 (three separate buy entries), one exit would sell ~300k tokens but delete all three entries (~916k tokens tracked). Remaining ~616k tokens become orphaned on-chain with no state tracking.
**Fix**: Remove only the first matching position entry (set `found` flag, skip first match, keep rest).
**Regression test**: After exiting one position of a multi-entry token, check that remaining entries for that mint still exist in state.json.

### 17. Grok Hallucinating Balances and Position Counts in latest.md
**File**: state/latest.md (written by Grok), HEARTBEAT.md, cron/jobs.json
**Bug**: `state/latest.md` was written manually by Grok after each heartbeat. Grok parsed the heartbeat_runner JSON output and "summarized" it into a human-readable markdown file. As an LLM, Grok hallucinated numbers: reported 8.5 SOL when state.json had 12.6 SOL, reported 11 positions when state had 14, reported sold positions (Walf, Miruru, RENT) as still held. Since Grok reads latest.md for orientation in Step 2 of each heartbeat, it started each cycle with wrong data, compounding the hallucination.
**Fix**: `stage_finalize()` in heartbeat_runner.py now auto-generates `state/latest.md` deterministically from state.json. HEARTBEAT.md and the cron job payload updated to instruct Grok to READ latest.md but NEVER overwrite it. Grok still writes checkpoint.md (thesis/strategy — subjective content where LLM reasoning is appropriate).
**Regression test**: Compare `state/latest.md` balance to `state/state.json` `current_balance_sol`. If they differ, something is overwriting latest.md. Check `ls -la state/latest.md` timestamp vs `last_heartbeat_time` in state.json — they should be within seconds.

---

## Boar Wrapper

**File**: `/home/autistboar/chadboar/boar` (bash, chmod +x)

```bash
#!/usr/bin/env bash
cd /home/autistboar/chadboar || exit 1
set -a; source .env 2>/dev/null; set +a
exec .venv/bin/python3 "$@"
```

Why: Gateway HTML-encodes shell metacharacters (`&&` -> `&amp;&amp;`). Boar avoids shell syntax entirely. ALL Python invocations MUST go through boar.

## Diagnostic Commands

```bash
# Wallet balance check
boar -c "import asyncio, httpx, os
rpc = f'https://mainnet.helius-rpc.com/?api-key={os.environ[\"HELIUS_API_KEY\"]}'
async def c():
  async with httpx.AsyncClient() as cl:
    r = await cl.post(rpc, json={'jsonrpc':'2.0','id':1,'method':'getBalance','params':['B1rUxZxotGKLaoaLcs3evctfJFP6YvywRwCaech8wjRo']})
    print(f'{r.json()[\"result\"][\"value\"]/1e9:.4f} SOL')
asyncio.run(c())"

# Token balance check (verify positions are real)
# NOTE: PumpFun tokens use Token-2022, NOT standard SPL Token
boar -c "import asyncio, httpx, os
rpc = f'https://mainnet.helius-rpc.com/?api-key={os.environ[\"HELIUS_API_KEY\"]}'
async def c():
  async with httpx.AsyncClient() as cl:
    for prog, label in [('TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA','SPL'),('TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb','Token2022')]:
      r = await cl.post(rpc, json={'jsonrpc':'2.0','id':1,'method':'getTokenAccountsByOwner','params':['B1rUxZxotGKLaoaLcs3evctfJFP6YvywRwCaech8wjRo',{'programId':prog},{'encoding':'jsonParsed'}]})
      accs = r.json()['result']['value']
      held = [a for a in accs if float(a['account']['data']['parsed']['info']['tokenAmount'].get('uiAmountString','0')) > 0]
      print(f'{label}: {len(held)} token accounts with balance')
      for a in held:
        info = a['account']['data']['parsed']['info']
        print(f'  {info[\"mint\"][:12]} = {info[\"tokenAmount\"][\"uiAmount\"]}')
asyncio.run(c())"

# Signer test
boar -c "from lib.signer.keychain import get_public_key; print(get_public_key())"

# Birdeye test
boar -c "import asyncio; from lib.clients.birdeye import BirdeyeClient
async def t():
  b = BirdeyeClient()
  r = await b.get_token_overview('So11111111111111111111111111111111111111112')
  print(f'Birdeye OK: price={r.get(\"data\",r).get(\"price\",0)}')
  await b.close()
asyncio.run(t())"

# Guard checks
boar -m lib.guards.killswitch
boar -m lib.guards.drawdown
boar -m lib.guards.risk

# Paper trade status
boar -c "import json; pt=json.load(open('state/paper_trades.json'))
o=[t for t in pt if not t.get('closed')]; c=[t for t in pt if t.get('closed')]
print(f'Open: {len(o)}, Closed: {len(c)}, Total: {len(pt)}')"

# Bead chain stats
boar -c "import sqlite3; c=sqlite3.connect('state/beads.db')
for r in c.execute('SELECT bead_type, count(*) FROM beads GROUP BY bead_type ORDER BY count(*) DESC').fetchall():
  print(f'  {r[0]:22s} {r[1]:>6d}')
c.close()"

# Gateway restart
systemctl --user restart openclaw-gateway.service
systemctl --user status openclaw-gateway.service --no-pager
```

## Common Failure Modes

| Symptom | Root Cause | Fix |
|---------|-----------|-----|
| Birdeye 401 | API key not loaded (wrong python, no dotenv) | Use `boar` wrapper, verify .env |
| "No signer key source" | SIGNER_KEY_PATH env var missing | Source .env or use boar wrapper |
| PnL showing millions% | Unit mismatch — comparing Jupiter price to Birdeye price | Use market cap PnL only |
| Positions in state but 0 token accounts | Phantom positions — tx never confirmed | Check on-chain, clear phantoms |
| "9 FAILED RPC sig" exits | Trying to sell tokens wallet doesn't hold | Verify on-chain balances first |
| Paper trades never closing | All 1380 "open" | Check `closed` field not `status` |
| Balance inflated | Phantom PnL from unit mismatch added to balance | Reset to on-chain SOL balance |
| Grok reports "Pos:0" but state has positions | Grok misreading heartbeat result or on-chain state | Check state.json vs on-chain |
| "observe_only" every cycle | Timeout before watchdog/oracle completes | Check API response times |
| Scoring always DISCARD | Volume gate or broken oracle | Check nansen/pulse API keys |
| Tx submitted but "not confirmed after 32s" | Bad signature (simulate with sigVerify:True) or low priority fee | Check signer uses VersionedTransaction constructor, not sign_message+populate |
| Positions vanish after heartbeat | stage_finalize overwrites state with stale dict | Ensure finalize re-reads state from disk before writing |
| Trade execution takes >30s | Dead Jito path adding 15s timeout | Jito removed; go direct to Helius RPC with retries |
| SL exit fails with Custom 6024 every heartbeat | Fixed 500bps (5%) slippage too low for cratered micro-caps | stage_execute_exits now escalates: 500→1500→4900 bps for critical/high urgency exits |
| total_wins/total_losses stuck at 0 despite exits | stage_execute_exits never updated win/loss counters | Win/loss tracking added to stage_execute_exits (same logic as pulse_quick_scan) |
| Full exit deletes ALL positions for a mint | `p["token_mint"] != mint` filter removes duplicate entries (XMN x3) | Changed to remove only the first matching position entry |
| Grok reports wrong balance/positions in Telegram | latest.md written by LLM (Grok hallucinates numbers) | latest.md auto-generated by heartbeat_runner from state.json; Grok reads but never writes it |

## Key Invariants

1. **INV-BLIND-KEY**: Private key NEVER in agent env. Signer is subprocess.
2. **INV-RUG-WARDEN-VETO**: Warden FAIL = NO trade, no override possible.
3. **INV-HUMAN-GATE-100**: Trades > $100 require G approval.
4. **INV-DRAWDOWN-50**: Pot < 50% starting -> halt 24h.
5. **INV-DAILY-EXPOSURE-30**: Max 30% of pot deployed per day.
6. **STATE-CHAIN MATCH**: positions[] MUST match on-chain token balances. current_balance_sol MUST match on-chain SOL balance. If mismatch, state is corrupted.
7. **MARKET-CAP PNL**: NEVER use per-token price for PnL. Always use entry_market_cap_usd vs current market cap.
8. **TX CONFIRMATION**: NEVER update state until tx confirmed on-chain via getSignatureStatuses.
9. **PAPER TRADE CLOSING**: Uses `closed` boolean field, NOT `status` field.
10. **SIGNER METHOD**: MUST use `VersionedTransaction(msg, [keypair])` constructor. NEVER use `keypair.sign_message()` + `VersionedTransaction.populate()` — produces invalid signatures.
11. **FINALIZE RE-READ**: `stage_finalize()` MUST re-read state from disk before writing. The `state` dict passed from `run_heartbeat()` is stale after execution writes positions.
12. **NO JITO WITHOUT TIP**: Jito bundles require a tip transaction. Without it, bundles are always rejected. Use direct RPC submission instead.
13. **ESCALATING SLIPPAGE ON SL**: `stage_execute_exits` MUST use escalating slippage (500→1500→4900 bps) for critical/high urgency exits. Micro-cap tokens that trigger SL have thin liquidity — fixed 5% slippage causes infinite retry loops where Custom 6024 fails every heartbeat while the position bleeds to zero.
14. **EXIT WIN/LOSS TRACKING**: `stage_execute_exits` MUST update `total_wins`, `total_losses`, `consecutive_losses`, and `daily_loss_pct` after each exit. Without this, the circuit breaker (3 consecutive losses = 50% size reduction) is blind to watchdog exits.
15. **SINGLE POSITION REMOVAL ON EXIT**: Full exits MUST remove only the FIRST matching position entry for a mint, NOT all entries. Multiple buys of the same token create separate position entries (e.g. XMN x3). Removing all entries on a single exit deletes unrelated positions and orphans on-chain tokens.
16. **PER-MINT POSITION LIMIT**: `stage_score_and_execute` MUST skip AUTO_EXECUTE if the mint already has >= 2 entries in `state.positions`. Prevents duplicate-mint stacking (e.g. XMN x4, Crabal x3) which concentrates risk and wastes tx fees on marginal re-entries.
17. **BIRDEYE TRADE LIMIT**: `get_trades()` MUST cap `limit` at 50 (`min(limit, 50)`). Birdeye API returns 400 for limit > 50. Without the cap, every volume concentration check fails, degrading warden validation.
18. **GRADUATION MCAP CAP**: Graduation plays above `max_mcap_graduation` ($500k default) are VETO'd. Tokens above $500k should be accumulation plays, not graduation speed plays.
19. **TOKEN-2022 FOR ON-CHAIN CHECKS**: PumpFun tokens use Token-2022 program (`TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb`), NOT standard SPL Token (`TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA`). Diagnostic commands checking `getTokenAccountsByOwner` MUST query Token-2022 or they will show 0 accounts.
20. **LATEST.MD IS AUTO-GENERATED**: `state/latest.md` is written deterministically by `stage_finalize()` in heartbeat_runner.py from state.json data. Grok MUST NOT overwrite it. Previously Grok wrote latest.md itself and hallucinated balances (8.5 vs 12.6 SOL actual) and position counts (11 vs 14 actual). The cron job and HEARTBEAT.md instruct Grok to READ latest.md for its report but never write it.
