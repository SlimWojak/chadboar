# ChadBoar Diagnostic: Zero Trades in 48+ Hours
**Date:** 2026-02-16 ~12:00 UTC | **Analyst:** Claude (Opus) via Claude Code

---

## Executive Summary

**The pipeline has produced zero candidates to score in 48+ hours. This is not a scoring threshold problem — it's a data source problem. All three primary data feeds are broken or degraded:**

| Source | Status | Failure Mode |
|--------|--------|-------------|
| Nansen Token Screener | **BROKEN (404)** | Endpoint `/token-screener` returns 404. Fallback `dex-trades` returns raw trades that don't parse into candidates. |
| Mobula Pulse (graduation scanner) | **DEAD (0 results)** | API authenticates (200 OK) but returns empty data set regardless of parameters. |
| Birdeye / Narrative Hunter | **BUG (parsing failure)** | Birdeye API works and returns trending tokens, but `narrative_scan.py` has a response parsing bug that drops all results silently. |
| Mobula Whale Wallets | **SILENT** | 5 hardcoded addresses returning 0 accumulation signals. Whales may not be active on memecoins. |

**Bottom line:** The scoring pipeline (thresholds, weights, conviction logic) is irrelevant because zero candidates ever reach it. The funnel dies at the data ingestion layer.

---

## Part 1: Funnel Trace (Last 24h)

### Every Heartbeat Cycle: Identical

Across 16+ heartbeat cycles (5-min intervals) and continuous 3-min pulse scans from 2026-02-15 ~12:00 UTC to 2026-02-16 ~12:00 UTC:

| Metric | Value |
|--------|-------|
| Heartbeat cycles run | ~288 (5-min intervals over 24h) |
| Pulse scans run | ~480 (3-min intervals over 24h) |
| Total candidates found (all sources) | **0** |
| Highest score achieved | **N/A** (nothing to score) |
| Tokens reaching conviction scorer | **0** |
| Tokens reaching rug warden | **0** |
| Tokens reaching edge bank | **0** |
| Trades executed | **0** |

### Per-Source Breakdown (Every Cycle)

```
Nansen screener (1h):  0 candidates  → 404 endpoint
Nansen screener (24h): 0 candidates  → 404 endpoint
Nansen dex-trades:     0 candidates  → fallback returns trades, parser extracts 0
Mobula Pulse:          2 raw → 0 after filters  (API returning near-empty)
Mobula Whale Scan:     0 accumulating  (5 wallets, none active)
Birdeye Narrative:     0 signals  (API works, parsing bug drops all tokens)
```

### The Funnel Doesn't Narrow — It's Empty at the Top

```
DATA SOURCES ──→ CANDIDATES ──→ SCORER ──→ RECOMMENDATION ──→ TRADE
     ↓                ↓            ↓             ↓              ↓
   BROKEN             0            0           N/A            N/A

Candidates die HERE ─┘
(never born)
```

There are no "almost made it" candidates scoring 40-60. There are no candidates at all. The funnel is empty from the first stage.

---

## Part 2: Retroactive Analysis

### What Actually Happened on Solana Memecoins (Last 24h)

DexScreener API confirmed active PumpFun graduation activity:

| Token | Contract | Peak Move | Timeframe | Mcap at Peak | Outcome |
|-------|----------|-----------|-----------|-------------|---------|
| **OTOME** | `B4xW...xQpump` | **+710%** | 6h | $381K | Active pump, pulling back |
| **MOCHI** | `8yyB...opump` | +559% (PF) | <6h | $5.4K now | Crashed -85% (pump-dump) |
| **KOSUKE** | `62eF...h2pump` | +1338% (PF) | <6h | $8.2K now | Crashed -77% (pump-dump) |
| **BINGWU** | `DMYNp...i6pump` | Recovery bounce | 6h | $1.5M | Established, +13.66% recovery |

**Were these in our firehose?** No. Zero of these tokens appeared in any scan.

### Root Cause Analysis Per Source

**Nansen — Would these tokens show whale accumulation?**
- Moot. The screener endpoint returns 404. Even if whales were buying OTOME, we'd never know.
- The `dex-trades` fallback returns individual trades but `_parse_screener_candidates()` expects screener-format data (token-level aggregates with `smart_money_wallets` field). The raw trades don't have this.

**Mobula Pulse — Would these tokens appear as graduates?**
- OTOME, MOCHI, KOSUKE all have `pump` suffix contracts → PumpFun graduates.
- They SHOULD have appeared in Pulse's bonding/bonded listings during their graduation window.
- But Pulse returns 0 results. The API may have changed, rate-limited the free tier, or the `a6249ed0` key may be deprecated.
- Tested with `blockchain=solana`, `blockchain=Solana`, `chain=solana`, and no chain filter — all return empty.

**Birdeye — Would narrative scan have caught the volume spikes?**
- Birdeye's trending endpoint returns active tokens (confirmed: monk, CARDS, OPENCLAW, OPN, LABUBU were trending at time of test).
- But `narrative_scan.py` line 51 does:
  ```python
  tokens = trending.get("data", trending.get("items", []))
  ```
- Birdeye returns `{"data": {"updateTime": ..., "tokens": [...], "total": N}}`.
- `trending.get("data")` returns the inner dict `{"tokens": [...]}`, which is a **dict not a list**.
- Line 52: `if isinstance(tokens, list)` → **False**. The for-loop never runs. All tokens silently dropped.
- **This is the critical parsing bug.** Fix is one line.

**Mobula Whale Wallets — Were these whales buying anything?**
- The 5 hardcoded addresses may be legitimate whales but aren't active on fresh PumpFun memecoins.
- The `accum_24h > $10K` filter is appropriate for accumulation plays but these wallets simply aren't accumulating.
- Coverage problem: 5 wallets is too narrow for a market with 37,000 new tokens/day.

### Diagnosis: Coverage Problem AND Integration Bugs

It's not one thing — it's compounding failures:

1. **Nansen screener: Dead endpoint** — no smart money discovery at all
2. **Mobula Pulse: Empty API** — no graduation detection
3. **Birdeye narrative: Parsing bug** — working data silently discarded
4. **Whale wallets: Too narrow** — 5 wallets can't cover this market

---

## Part 3: Tactical Options

### Option A: Fix the Three Broken Pipes (Recommended First)

**Effort:** 30-60 minutes | **Risk:** Low | **Impact:** Restores baseline functionality

1. **Fix Birdeye narrative parsing bug** (5 min):
   ```python
   # narrative_scan.py line 51 — BEFORE (broken):
   tokens = trending.get("data", trending.get("items", []))

   # AFTER (fixed):
   raw = trending.get("data", {})
   tokens = raw.get("tokens", raw) if isinstance(raw, dict) else raw
   ```
   This alone restores Birdeye volume spike detection for trending tokens.

2. **Fix or replace Nansen screener** (15 min):
   - The `/token-screener` endpoint returns 404 — likely deprecated or renamed.
   - **Quick fix:** Pivot the discovery to use `dex-trades` (which works) and rewrite `_parse_screener_candidates()` to extract token mints from trade data, then aggregate by token.
   - **Better fix:** Check Nansen docs for the current screener endpoint. The API key is valid (dex-trades works).

3. **Diagnose Mobula Pulse** (15 min):
   - Check if the Pulse v2 API requires a different auth method or has moved endpoints.
   - The key `a6249ed0-148b-46d3-9c85-e42ac553adb2` authenticates (200 OK) but returns empty.
   - Could be: free tier limit, API version change, or the Pulse product was deprecated/paywalled.
   - **Fallback:** If Pulse is dead, add DexScreener's `/token-boosts/top/v1` as a graduation signal source (free, no auth, shows freshly promoted PumpFun tokens).

**Tradeoff:** No new risk exposure. Just restores what was supposed to work. Won't generate trades by itself if the underlying market data is sparse, but at least candidates will enter the funnel.

### Option B: Lower Thresholds + Paper Trading Mode

**Effort:** 20 min | **Risk:** Medium (requires careful monitoring) | **Impact:** Gets the learning flywheel spinning

After fixing Option A, if candidates start appearing but scoring 40-60:

1. **Add paper trading tier** (score 50-64):
   - Log phantom trades at WATCHLIST-minus level
   - Track hypothetical entry/exit, P&L, timing
   - Write beads for learning without capital risk
   - New recommendation: `PAPER_TRADE` for scores 50-64

2. **Lower graduation AUTO_EXECUTE to 55** (from 65):
   - Graduation plays are already capped at $50/trade
   - At 14 SOL pot, a $50 loss is 0.5% — recoverable
   - Lets the system learn from real execution feedback

3. **Lower accumulation WATCHLIST to 50** (from 60):
   - More tokens get tracked and scored over time
   - Edge bank starts building pattern data

**Tradeoff:** More phantom/micro trades, some will be losers. But the edge bank stays empty at 0 beads forever if nothing ever trades. The learning cost is small with graduation's $50 cap.

### Option C: Micro-Scalp Configuration

**Effort:** 30 min | **Risk:** Low-Medium | **Impact:** Small real trades for learning data

1. **Add a "micro-scalp" play type** with:
   - Position size: $5-10 USD (0.06-0.12 SOL)
   - Conviction threshold: 45+ permission score
   - Max 5 micro-scalps per day
   - 15-minute time decay (faster exits)
   - No human gate (too small to matter)

2. **Source from Birdeye trending** (once parsing is fixed):
   - Any trending token with >5x volume spike and Rug Warden PASS
   - Score it, and if >45, open a $5 position
   - Track results, build edge bank data

**Tradeoff:** ~$5-50/day capital at risk. Most will be noise. But you get:
- Real execution testing (Jupiter swaps, slippage, timing)
- Edge bank beads (historical patterns for future scoring)
- Confidence that the pipeline actually works end-to-end
- Data to calibrate thresholds empirically rather than theoretically

### Option D: Expand Token Universe

**Effort:** 1-2 hours | **Risk:** Low | **Impact:** More coverage

1. **Add DexScreener as a data source:**
   - `/token-boosts/top/v1` — freshly promoted tokens (free API, no auth)
   - `/token-profiles/latest/v1` — new token profiles
   - High correlation with imminent pumps (projects pay to boost before launch)

2. **Expand whale wallet list:**
   - Current: 5 hardcoded addresses → too narrow
   - Use Nansen's `dex-trades` (which works) to dynamically identify active Solana smart money wallets
   - Build a rolling "hot wallets" list updated daily

3. **Add PumpFun direct monitoring:**
   - PumpFun has a public API for graduating tokens
   - Doesn't require Mobula as intermediary

**Tradeoff:** More code to maintain. But addresses the root coverage problem — 5 static wallets and one broken Pulse API can't monitor a market with 37K new tokens/day.

---

## Priority Recommendation

```
IMMEDIATE  (today):  Option A — Fix the three broken pipes
                     This is blocking everything else.

NEXT       (today):  Option B — Add paper trading tier
                     Start capturing what-if data immediately.

THEN       (48h):   Option C — Enable micro-scalps
                     Real execution data with minimal risk.

LATER      (week):  Option D — Expand coverage
                     More data sources for better signal.
```

The scoring pipeline design is sound. Thresholds are reasonable. The conviction framework works. But it's been starved of input data for 48+ hours because three data integrations are broken simultaneously. Fix the plumbing, and the rest of the system should start functioning.

---

## Appendix: Quick-Fix Commands

### Fix Birdeye Parsing (narrative_scan.py line 51)
```python
# Replace:
tokens = trending.get("data", trending.get("items", []))
# With:
raw_data = trending.get("data", {})
tokens = raw_data.get("tokens", raw_data) if isinstance(raw_data, dict) else raw_data
```

### Test After Fix
```bash
# Should return signals now:
boar -m lib.skills.narrative_scan

# Oracle (will still be limited until Nansen/Pulse fixed):
boar -m lib.skills.oracle_query

# Pulse (blocked on Mobula API investigation):
boar -m lib.skills.pulse_quick_scan
```
