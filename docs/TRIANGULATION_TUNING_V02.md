# Triangulation Tuning v0.2 — Implementation Complete

**Date:** 2026-02-11  
**Status:** ✅ ALL 6 PHASES COMPLETE  
**Philosophy:** Asymmetric risk — fewer stupid losses, not zero losses

---

## Summary

Implemented full triangulation tuning directive from G's v0.2 plan:
- **Permission Gate (A1):** ≥2 PRIMARY sources required for AUTO_EXECUTE
- **Partial Data Penalty (A2):** Uncertainty penalties + observe-only mode
- **Red Flags (B1):** Volume concentration (−15 pts), dumper wallets (−15/−30 pts or VETO)
- **Time Mismatch (B2):** Oracle + Narrative <5min → downgrade by 1 tier
- **Ordering vs Permission Split (C1):** Both scores logged, permission governs action
- **Veto Expansion:** 5 vetoes total (Rug Warden FAIL, all dumpers, token <2min, wash trading, [liquidity drop TODO])

---

## Design Principles

1. **Asymmetry Preserved:** High-quality setups (3+ whales + 10x volume + KOL + clean warden) still AUTO_EXECUTE
2. **Stupidity Reduced:** Narrative-only blocked, partial data handled gracefully, dumpers vetoed
3. **Learning Surface Maintained:** Ordering score preserved in beads even when vetoed/downgraded

---

## Architecture Changes

### Core Files Modified

**1. lib/scoring.py** (full rewrite)
- Added `calculate_ordering_score()` — original "greedy" conviction logic
- Added `calculate_permission_score()` — penalized version with red flags
- Added `apply_permission_gate()` — constitutional ≥2 PRIMARY check
- Added `check_vetoes()` — 5 veto conditions
- Split final output: `OrderingScore + PermissionScore + FinalDecision`
- Beads log both scores for learning

**2. lib/heartbeat_runner.py**
- Added partial data tracking (`oracle_missing`, `narrative_missing`, `warden_missing`)
- Added time mismatch detection (`signal_times` dictionary)
- Added red flag fetching (`get_volume_concentration()`, `get_dumper_wallets()`)
- Pass red flags to scorer

**3. lib/clients/birdeye.py**
- Added `get_trades(mint, limit=100)` — fetch recent swap events
- Returns list of trades with timestamp, side, amount, wallet

**4. lib/clients/nansen.py**
- Added `get_wallet_transaction_history(wallet, limit=100)` — fetch wallet's sell history
- Returns list of transactions with timestamp, action, token

**5. lib/utils/red_flags.py** (NEW)
- `detect_volume_concentration(trades)` — Gini coefficient on trade volume distribution
- `detect_dumper_wallets(trades, nansen_client)` — check for wallets with 3+ dumps in 7d

---

## Signal Definitions

### PRIMARY Sources
- **Oracle:** ≥3 whales accumulating (threshold from original scoring.py)
- **Narrative:** ≥5x volume spike (threshold from original heartbeat_runner.py)

### Partial Data Penalties
- Missing Oracle: 0.7x multiplier on whales score
- Missing Narrative: 0.8x multiplier on volume spike score
- Missing Warden: Observe-only mode (cannot AUTO_EXECUTE)
- ≥2 sources failed: Force observe-only mode

---

## Red Flags

### Volume Concentration (−15 pts)
- Gini coefficient ≥0.8 on last 100 trades
- Indicates few wallets controlling volume → manipulation risk

### Dumper Wallets
- Any wallet in top 10 trades with ≥3 sells in 7d:
  - **1-2 dumpers:** −15 pts (warning)
  - **≥3 dumpers:** VETO (likely coordinated dump)

---

## Vetoes (5 Total)

1. **Rug Warden FAIL** (original)
2. **All Dumpers** (≥3 wallets with dump history)
3. **Token <2min old** (NEW — too early for reliable signals)
4. **Wash Trading** (NEW — ≥10x volume spike + no KOL detected)
5. **Liquidity Drop** (TODO — if liquidity drops >50% during scoring)

---

## Conviction Tiers (Permission Score)

| Tier | Score Range | Action | Requirements |
|------|-------------|--------|--------------|
| VETO | N/A | Do not trade | Any veto condition triggered |
| DISCARD | <60 | Ignore | Low conviction, no alert |
| WATCHLIST | 60-84 | Log + alert G | Interesting but not auto-tradeable |
| AUTO_EXECUTE | ≥85 | Auto-trade | ≥2 PRIMARY sources + no vetoes |

---

## Time Mismatch Detection

If Oracle signal and Narrative signal both exist but timestamps differ by <5 minutes:
- **Downgrade by 1 tier:**
  - AUTO_EXECUTE → WATCHLIST
  - WATCHLIST → DISCARD
- **Rationale:** Likely same event triggering both sources → signals not independent

---

## Test Results

All test cases passing:

```bash
# Clean setup (3 whales + 10x volume + KOL + PASS)
Ordering: 90 → Permission: 90 → AUTO_EXECUTE ✅

# Only 1 primary source (narrative-only, 10x volume + KOL)
Ordering: 64 → Permission: 64 → WATCHLIST (permission gate blocks AUTO) ✅

# Concentrated volume (3 whales + 10x volume + KOL, 0.85 Gini)
Ordering: 90 → Permission: 75 → WATCHLIST (red flag penalty) ✅

# All dumpers (3 whales + 10x volume + KOL, 4 dumpers)
Ordering: 90 → Permission: VETO (ordering preserved for learning) ✅

# Time mismatch (3 whales + 10x volume + KOL, 3min gap)
Ordering: 90 → Permission: 90 → WATCHLIST (downgraded from AUTO) ✅

# Token <2min old
Ordering: 90 → Permission: VETO ✅

# Wash trading (15x volume + no KOL)
Ordering: 94 → Permission: VETO ✅
```

---

## Bead Logging Enhancement

Every trade autopsy bead now includes:

```yaml
conviction_breakdown:
  ordering_score: 90
  permission_score: 75
  ordering_breakdown:
    whales_score: 45
    volume_score: 30
    kol_bonus: 10
    narrative_age_bonus: 5
  permission_breakdown:
    base_score: 90
    red_flags:
      - type: volume_concentration
        penalty: -15
        gini: 0.85
  vetoes_checked: [rug_warden, dumpers, age, wash_trading, liquidity]
  vetoes_triggered: []
  primary_sources: [oracle, narrative]
  partial_data_penalties: []
  time_mismatch_detected: false
```

This gives us rich learning data for future tuning.

---

## Dry-Run Validation Gates

Before going live, validate across 10 dry-run cycles:

**Gate A (Asymmetry Preserved):**
- High-quality setups (3+ whales + 10x volume + KOL) should still AUTO_EXECUTE
- Expect: ≥1 AUTO_EXECUTE recommendation per 10 cycles (if market provides signals)

**Gate B (Stupidity Reduced):**
- Narrative-only setups should WATCHLIST (not AUTO)
- Partial data setups should degrade gracefully
- Dumpers should VETO

**Gate C (Learning Surface):**
- Beads should log both ordering + permission scores
- Red flags should be documented
- Downgrade reasons should be clear

---

## Next Steps

1. **Trigger 10-cycle dry-run period** (G to initiate)
2. **Review beads after 10 cycles** to validate Gates A-C
3. **If gates pass:** Switch to live trading
4. **If gates fail:** Adjust thresholds and re-test

---

## Files Changed

**Modified:**
- `lib/scoring.py` (full rewrite)
- `lib/heartbeat_runner.py` (partial data tracking, red flag fetching)
- `lib/clients/birdeye.py` (added get_trades())
- `lib/clients/nansen.py` (added get_wallet_transaction_history())

**New:**
- `lib/utils/red_flags.py` (volume concentration + dumper detection)
- `docs/TRIANGULATION_TUNING_V02.md` (this file)

**Preserved:**
- Original conviction logic as `ordering_score` for learning
- All acceptance gates (6/6 passing)
- Heartbeat time budget (2min target)
- Atomic state writes with file locking
