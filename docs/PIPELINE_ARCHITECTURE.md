# Trade Pipeline Architecture — M2M Summary

**Version:** 1.0 (post-conviction-scoring)  
**Purpose:** Dense technical overview for optimization analysis

---

## Data Flow

```
Heartbeat (10min) → Guards (killswitch/drawdown/risk) → Signal Detection (Oracle+Narrative) 
→ Conviction Scoring → Decision (VETO/DISCARD/WATCHLIST/EXECUTE) → State Update → Checkpoint
```

Every 10 minutes, full cycle or blocked by guard. No partial states.

---

## 1. Signal Detection Layer

### Smart Money Oracle (Nansen API)
- **Endpoint:** `/smart-money/dex-trades` (POST, 50 trades/query)
- **Filter:** `token_sold == SOL && token_bought != token` (infers BUY)
- **Aggregation:** Group by token_mint, count distinct `trader_address` wallets
- **Threshold:** ≥3 wallets = signal emitted
- **Output:** `{token_mint, token_symbol, wallet_count, total_buy_usd, confidence}`
- **Latency:** ~2-4s per query
- **Rate Limit:** 2 req/s (BaseClient enforces)

### Narrative Hunter (Birdeye + X API)
- **Birdeye:** `/token/overview` → volume metrics (v1hUSD, v24hUSD)
- **Volume Calc:** `spike = (v1h / (v24h/24))` — hourly vs 24h average
- **X Search:** Recent mentions `${symbol} OR {symbol} solana` (50 results)
- **KOL Detection:** Filter users with `followers_count >= 10000`
- **Threshold:** `spike >= 5.0x` = signal emitted
- **Output:** `{token_mint, volume_vs_avg, x_mentions_1h, kol_mentions}`
- **Latency:** ~3-6s (Birdeye 1s, X 2-5s)
- **Rate Limit:** Birdeye 2/s, X 300/15min window

### Signal Merge Strategy
- Union of mints from Oracle + Narrative
- Per-token: join by `token_mint`, carry forward all fields
- Missing data defaults: `whales=0`, `volume_spike=0`, `kol=false`

---

## 2. Pre-Trade Validation (Rug Warden)

**Invoked:** Before scoring, per candidate token  
**Module:** `lib/skills/warden_check.py --token <MINT>`  
**Checks:**
1. Liquidity > $10k (Birdeye `/token/overview` → liquidity pool data)
2. Holder concentration < 80% (top 10 wallets, Helius `/addresses/balances`)
3. Mint authority revoked (`mint.mintAuthority == null`)
4. Freeze authority revoked (`mint.freezeAuthority == null`)
5. LP locked/burned (Helius token metadata + burn detection)
6. Honeypot simulation: dry-run sell tx (Helius simulate)

**Verdicts:**
- `PASS`: All checks green → proceed to scoring
- `WARN`: Non-critical issues (e.g. moderate concentration) → proceed with penalty
- `FAIL`: Critical risk → scoring returns `VETO`, trade blocked (INV-RUG-WARDEN-VETO)

**Latency:** ~2-3s (multiple Helius + Birdeye calls)  
**Caching:** None currently (every signal re-validates)

---

## 3. Conviction Scoring Algorithm

**Module:** `lib/scoring.py`  
**Input:** `SignalInput(whales, volume_spike, kol, narrative_age_min, rug_status, edge_match_pct)`  
**Output:** `ConvictionScore(total, breakdown, recommendation, position_size_sol, reasoning)`

### Weights & Formula

| Component | Max Points | Algorithm |
|-----------|-----------|-----------|
| Smart Money Oracle | 40 | `min(whales × 15, 40)` (requires 3+ for max) |
| Narrative Hunter | 30 | Volume: `min((spike/5)×15, 25)` + KOL: `+5` + Age decay: `-1/min after 30min` |
| Rug Warden | 20 | PASS=20, WARN=10, FAIL=veto (score forced to 0) |
| Edge Bank | 10 | `(match_pct / 100) × 10` (vector similarity to past winners) |

**Total:** 0-100 scale

### Decision Thresholds

| Score | Recommendation | Action |
|-------|----------------|--------|
| 0 (rug FAIL) | VETO | Blocked, no alert |
| 1-59 | DISCARD | Logged, no alert |
| 60-84 | WATCHLIST | 🟢 INFO alert to G with breakdown |
| 85-100 | AUTO_EXECUTE | Subject to tier gates (see below) |

### Position Sizing

```python
base_size = (score / 100) × (pot × 0.01)
adjusted_size = base_size × (1 / volatility_factor)
final_size = min(adjusted_size, pot × 0.05)  # Cap at 5% per trade
```

**Example:** Score 90, pot 14 SOL, volatility 1.0 → `(90/100)×(14×0.01)×1 = 0.126 SOL` (< 0.7 cap)

**Volatility:** Currently hardcoded to 1.0, intended for dynamic calc from price history

---

## 4. Decision Gates (Post-Scoring)

### Tier Gates (INV-HUMAN-GATE-100)
Even if `recommendation == AUTO_EXECUTE`:

1. **Check position size USD:** `size_sol × sol_price_usd`
2. **If > $100:** Send 🟡 WARNING to G with full thesis, await approval via Telegram
3. **If ≤ $100:** Proceed to risk checks

### Risk Checks (INV-DAILY-EXPOSURE-30)
- **Daily exposure:** Sum of all `entry_amount_sol` where `entry_date == today`
- **Limit:** `pot × 0.30` (4.2 SOL at 14 SOL pot)
- **If `daily_exposure + new_size > limit`:** Block, send 🟡 WARNING

### Position Count
- **Open positions:** Count entries in `state.json["positions"]` with `status == "open"`
- **Limit:** 5 concurrent
- **If at limit:** Block new entries until exit

### Drawdown Guard (INV-DRAWDOWN-50)
- **Check:** `current_balance_sol < (starting_balance_sol × 0.5)`
- **If true:** Halt ALL trading for 24h, send 🔴 CRITICAL alert
- **State:** `halted: true`, `halted_at: timestamp`, `halt_reason: "drawdown"`

---

## 5. Execution Pathway

### Entry (Dry-Run Mode)
**Current:** Log decision, increment `dry_run_cycles_completed`, do NOT call swap
```python
if dry_run:
    log(f"DRY-RUN: would execute {size} SOL on {mint}")
else:
    execute_swap(...)
```

### Entry (Live Mode)
**Module:** `lib/skills/execute_swap.py --direction buy --token <MINT> --amount <SOL>`  
**Process:**
1. Construct unsigned Jupiter swap transaction (SOL → token)
2. Pass to blind signer subprocess via stdin (INV-BLIND-KEY)
3. Signer returns signed tx bytes
4. Submit via Jito bundle (MEV protection)
5. Poll tx status (max 60s timeout)
6. On confirm: update `state.json["positions"]`, write entry bead

**Latency:** 5-15s (Jupiter quote 1s, Jito submit 1-2s, confirm 3-12s)

### Bead Write (Trade Autopsy)
**Module:** `lib/skills/bead_write.py --type entry --data <JSON>`  
**Format:** Markdown file `beads/YYYYMMDD_HHMMSS_entry_<symbol>.md`  
**Contents:**
- Timestamp, token mint, entry amount, entry price
- Full conviction breakdown (whales, volume, KOL, age, rug, edge)
- Thesis (reasoning string from scorer)
- Market conditions snapshot (SOL price, pot size, daily exposure)

**Purpose:** Persistent record for Edge Bank vector search, G review, post-mortem

---

## 6. Position Management (Exit Logic)

**Invoked:** Step 7 of HEARTBEAT.md, runs on EVERY cycle (every open position checked)

### Exit Tiers

| Condition | Action | Rationale |
|-----------|--------|-----------|
| PnL ≤ -20% | Exit 100% immediately (stop-loss) | Hard floor, preserve capital |
| PnL ≥ +100% (2x) | Exit 50% of position | Take profit tier 1, lock gains |
| PnL ≥ +400% (5x) | Exit 30% of remaining | Take profit tier 2, let runner run |
| Peak drawdown > 20% | Exit 100% remaining | Trailing stop from peak |
| No price move >5% in 60min | Exit 100% | Time decay, momentum died |
| Liquidity drop >50% | Exit 100% | Rug risk, prepare escape |

### Exit Execution
**Same flow as entry, reversed:** token → SOL swap via Jupiter → Jito bundle

### Bead Write (Exit)
**Type:** `exit`  
**Additional fields:**
- Exit price, PnL %, holding duration
- Exit reason (stop-loss/tp1/tp2/trailing/time-decay/liquidity)
- Lessons learned (manual field for G input)

---

## 7. State Persistence

### state.json Schema
```json
{
  "starting_balance_sol": 14.0,
  "current_balance_sol": 14.0,
  "positions": [
    {
      "token_mint": "...",
      "token_symbol": "...",
      "entry_time": "ISO8601",
      "entry_amount_sol": 0.1,
      "entry_price": 0.00001,
      "peak_price": 0.00001,
      "current_price": 0.00001,
      "pnl_pct": 0.0,
      "status": "open|closed"
    }
  ],
  "daily_exposure_sol": 0.0,
  "daily_date": "2026-02-10",
  "halted": false,
  "dry_run_mode": true,
  "dry_run_cycles_completed": 3,
  "total_trades": 0,
  "total_wins": 0,
  "total_losses": 0,
  "last_heartbeat_time": "ISO8601"
}
```

**Update frequency:** Every heartbeat (10min)  
**Atomicity:** Full file rewrite (JSON dump), no partial updates

### checkpoint.md (Strategic Context)
```markdown
thesis: "WHAT the system is watching and WHY"
regime: green|yellow|red|halted
open_positions: N
next_action: "PRIORITY for next heartbeat"
concern: "ISSUE or 'none'"
```

**Purpose:** Persists strategic reasoning across agent spawns (OpenClaw session lifecycle)

### narrative_cache.json (Age Tracking)
```json
{
  "tokens": {
    "<MINT>": {
      "first_seen": "ISO8601",
      "last_seen": "ISO8601"
    }
  }
}
```

**Cleanup:** Auto-purge entries >24h old (narrative age decay complete)

---

## 8. Known Bottlenecks & Limitations

### Latency Chain (Per Heartbeat)
```
Guards: 0.1s
Oracle (Nansen): 2-4s
Narrative (Birdeye + X): 3-6s per token × N tokens (currently 5) = 15-30s
Rug Warden: 2-3s per candidate
Scoring: <0.01s
State write: <0.1s

Total: ~20-40s per heartbeat (mostly API I/O)
```

### API Rate Limits
- **Nansen:** 2 req/s (enforced by BaseClient)
- **Birdeye:** 2 req/s (enforced by BaseClient)
- **X:** 300 req/15min = 20/min (not enforced, relies on OpenClaw rate limiter)
- **Helius:** 10 req/s free tier (not enforced)

**Risk:** X API rate limit hit if scanning >20 tokens/cycle

### Missing Integrations
1. **Rug Warden not called in heartbeat_runner:** Currently hardcoded `rug_status = "PASS"`
2. **Edge Bank vector search:** No beads exist yet, `edge_match_pct = 0` always
3. **Execute_swap not wired:** Live execution path exists but not invoked in runner
4. **Volatility factor:** Hardcoded to 1.0, should derive from price history std dev
5. **Price updates:** Positions not repriced on every heartbeat (stale `current_price`)

### Scalability Constraints
- **Narrative scan limited to 5 tokens/cycle:** Hardcoded in `heartbeat_runner.py` (trending[:5])
- **No parallelization:** All API calls sequential (asyncio used but not concurrent)
- **State file lock:** JSON rewrite not atomic, race condition if manual edit during heartbeat
- **No retry logic:** API failures abort the signal for that cycle (no exponential backoff)

### Data Staleness
- **Oracle signals:** Nansen data already 5-30s old (blockchain finality + indexing lag)
- **Price data:** Birdeye updates every 10-30s, heartbeat every 10min → can miss micro-moves
- **X mentions:** Search recent (5min window), but API pagination may miss high-volume spikes

---

## 9. Optimization Vectors (Identified)

### Latency Reduction
1. **Parallel API calls:** Fetch Nansen + Birdeye + X concurrently (asyncio.gather)
2. **Rug Warden caching:** Cache verdicts for 5min (tokens don't change that fast)
3. **Streaming narrative scan:** Don't wait for all 5 tokens, score + decide as each completes
4. **Pre-fetch trending tokens:** Run Birdeye trending query at T-1min, ready for heartbeat at T

### Signal Quality
5. **Multi-timeframe volume:** Check 15min, 1h, 4h spikes (not just 1h vs 24h)
6. **Wallet cohort analysis:** Cluster whale wallets by on-chain behavior (Nansen labels)
7. **Narrative velocity:** Track mention growth rate (ΔX mentions per 10min) not just absolute
8. **Liquidity depth:** Check order book slippage ±5% (Jupiter quote API), not just total liquidity

### Decision Robustness
9. **Confidence intervals:** Add uncertainty bounds to conviction score (e.g. 85±7)
10. **Signal correlation penalty:** Deweight if same wallets appear across multiple tokens (coordinated pump)
11. **Historical context:** Compare current score to 7-day average for this token (baseline normalization)
12. **Counter-signal detection:** Track smart money SELLS as negative weight

### Execution Efficiency
13. **Dynamic slippage:** Adjust Jupiter slippage based on volatility + liquidity depth
14. **Partial fills:** Allow 50% entry if full size would exceed daily exposure
15. **Exit priority queue:** Process stop-losses before take-profits (risk management first)
16. **Gas optimization:** Batch multiple exits into single Jito bundle (if >1 position exits same cycle)

### State Management
17. **Incremental state updates:** Append-only position log instead of full JSON rewrite
18. **SQLite migration:** Queryable state (e.g. "show all positions with PnL > 50%")
19. **Checkpoint versioning:** Git-style diffs for checkpoint.md (track strategic pivots)
20. **Bead indexing:** Pre-compute vector embeddings on write (not at query time)

---

## 10. Invariant Enforcement Points

| Invariant | Guard Location | Enforcement Mechanism |
|-----------|----------------|----------------------|
| INV-BLIND-KEY | execute_swap.py | Subprocess I/O, key never in Python |
| INV-RUG-WARDEN-VETO | scoring.py:49 | `if rug_status == "FAIL": return VETO` |
| INV-HUMAN-GATE-100 | heartbeat_runner.py:141 | `if size_usd > 100: await_approval()` |
| INV-DRAWDOWN-50 | guards/drawdown.py | `if pot < start×0.5: halt()` |
| INV-KILLSWITCH | guards/killswitch.py | `if exists("killswitch.txt"): abort()` |
| INV-DAILY-EXPOSURE-30 | guards/risk.py | `if daily_exp > pot×0.3: block()` |

All guards run BEFORE scoring. Fail-closed (default deny).

---

## 11. Edge Cases & Failure Modes

### API Failures
- **Nansen down:** Oracle returns `[]`, scoring proceeds with `whales=0`
- **Birdeye down:** Narrative returns `[]`, scoring proceeds with `volume_spike=0`
- **X down:** KOL detection disabled, scoring loses max 5pts from that component
- **Helius down:** Rug Warden fails OPEN (returns WARN or FAIL, not PASS)

**Current behavior:** Graceful degradation (log error, continue with partial data)

### State Corruption
- **state.json malformed:** Heartbeat crashes, no recovery (manual fix required)
- **checkpoint.md missing:** Agent spawns cold, no strategic context (rebuilds from state.json)
- **narrative_cache.json deleted:** All ages reset to 0 (new narratives scored as fresh)

**No automatic repair:** Assumes state files are append-only or managed by single writer (heartbeat)

### Race Conditions
- **Manual state.json edit during heartbeat:** Last write wins (no locking)
- **Multiple heartbeats running:** Not prevented by system (should never happen with 10min cron)

### Signal Spoofing
- **Wash trading:** High volume from same wallets (not detected, Oracle counts unique wallets only)
- **Bot-generated X mentions:** Spam accounts inflate mention count (KOL filter helps, not immune)
- **Liquidity flash:** LP added before scan, removed after (Rug Warden snapshot check, vulnerable to timing)

**Mitigation:** Rug Warden checks + multi-signal convergence requirement

---

## 12. Data Lineage

```
Blockchain (Solana) 
  ↓ (indexed by)
Nansen, Birdeye, Helius
  ↓ (API calls from)
Smart Money Oracle, Narrative Hunter, Rug Warden
  ↓ (signals merged by)
Heartbeat Runner
  ↓ (scored by)
Conviction Scorer
  ↓ (decided by)
Decision Gates (tier/risk/drawdown)
  ↓ (executed by)
Blind Executioner (execute_swap)
  ↓ (recorded in)
Beads (autopsy) + state.json (positions)
  ↓ (persisted to)
GitHub repo (commit on state change)
  ↓ (recalled by)
Edge Bank (vector search over beads)
```

**Full cycle latency (entry to bead):** ~25-50s from signal detection to trade confirmation

---

**EOF** — Pipeline snapshot as of `94d6579`. Optimization analysis can target sections 9-12.
