# Triangulation Competence Audit & Tuning Proposal

**Date:** 2026-02-11  
**Mode:** Investigation → Proposal → Execution  
**Scope:** Multi-source data triangulation (Nansen + Birdeye + Helius)  

---

## 1. Current Triangulation Model (As-Is)

### Signal Sources
1. **Smart Money Oracle** (Nansen)  
   - Queries recent smart money DEX trades  
   - Aggregates by token mint: counts distinct whales, total buy volume  
   - Threshold: ≥3 whales accumulating → signal generated  

2. **Narrative Hunter** (Birdeye + X API)  
   - Trending tokens by volume (top 5 from Birdeye)  
   - For each: calculate volume spike (1h vs 24h avg)  
   - Scan X for mentions, count KOLs (≥10k followers)  
   - Threshold: ≥5x volume spike → signal generated  

3. **Rug Warden** (Birdeye security + Helius metadata)  
   - 6-point pre-trade validation:  
     - Liquidity ≥ $10k  
     - Top 10 holders < 80%  
     - Mint/freeze authority immutable  
     - Token age ≥ 5min  
     - LP locked/burned  
     - Honeypot sim (planned, not implemented)  
   - Returns: `PASS`, `WARN`, or `FAIL`  

### Signal Merging (lib/heartbeat_runner.py, lines 151-195)
```python
# Merge signals by token mint
all_mints = set()
for sig in oracle_signals:
    all_mints.add(sig["token_mint"])
for sig in narrative_signals:
    all_mints.add(sig["token_mint"])

for mint in all_mints:
    oracle_sig = next((s for s in oracle_signals if s["token_mint"] == mint), None)
    narrative_sig = next((s for s in narrative_signals if s["token_mint"] == mint), None)
    
    whales = oracle_sig["wallet_count"] if oracle_sig else 0
    volume_spike = parse_volume(narrative_sig) if narrative_sig else 0.0
    kol_detected = narrative_sig.get("kol_mentions", 0) > 0 if narrative_sig else False
    
    # Run Rug Warden
    rug_status = await run_rug_warden(mint)
    
    # Score
    signal_input = SignalInput(
        smart_money_whales=whales,
        narrative_volume_spike=volume_spike,
        narrative_kol_detected=kol_detected,
        narrative_age_minutes=tracker.get_age_minutes(mint),
        rug_warden_status=rug_status,
        edge_bank_match_pct=0.0,  # No beads yet
    )
    
    score = scorer.score(signal_input, pot_balance_sol)
```

### Conviction Scoring (lib/scoring.py, lines 60-120)
**Weighted aggregation:**
- Smart Money Oracle: +15 per whale, cap 40 (max at 3+ whales)  
- Narrative Hunter: 15-30 pts (volume spike scaled, +10 KOL bonus, 30min decay)  
- Rug Warden: +20 (PASS), +10 (WARN), 0 (FAIL → veto)  
- Edge Bank: 0-10 pts (70-100% match to past winners)  

**Thresholds:**
- ≥85: AUTO_EXECUTE  
- 60-84: WATCHLIST  
- <60: DISCARD  

**Veto logic (lib/scoring.py, lines 101-110):**
```python
if signals.rug_warden_status == "FAIL":
    return ConvictionScore(
        total=0,
        breakdown={"rug_warden": 0},
        recommendation="VETO",
        position_size_sol=0.0,
        reasoning="Rug Warden FAIL — trade vetoed (INV-RUG-WARDEN-VETO)"
    )
```

### Current Behavior Summary
- **Merging:** Union of oracle + narrative signals, run warden on each  
- **Weighting:** Additive scoring, 4 independent signals contribute to total  
- **Veto:** Rug Warden FAIL → instant veto, no trade  
- **Confidence:** Score is sum of weighted signals, recommendation is threshold-based  

---

## 2. Identified Frailties (Concrete, Code-Linked)

### F1: Single-Source Domination (lib/scoring.py, lines 60-85)
**Location:** `ConvictionScorer.score_smart_money_oracle()`  
**Issue:** Oracle alone can provide 40/85 points (47% of auto-execute threshold)  
**Failure Mode:**  
- 3 whales buy same token → 40 pts  
- Moderate narrative (20 pts) + Rug Warden PASS (20 pts) = 80 pts (WATCHLIST)  
- Add small edge match (5 pts) → **85 pts AUTO_EXECUTE**  
- **Risk:** Whales could be insider wallets or coordinated dump group  

**Evidence:** No cross-validation of whale identity or historical PnL  
**Code:**
```python
# No check if whales are dumpers, insiders, or wash traders
score = min(whales * 15, self.weights['smart_money_oracle'])
```

### F2: Narrative-Led Bias (lib/heartbeat_runner.py, lines 128-145)
**Location:** `scan_token_narrative()`  
**Issue:** Volume spike triggers narrative tracking, but spike could be from:  
- Insider pump before public announcement  
- Wash trading  
- Single large trade (not organic volume)  

**Failure Mode:**  
- Token sees 10x volume spike in 1h (insider buys)  
- Few X mentions, but KOL tweets it → 10 pts KOL bonus  
- Narrative score: 25 pts (volume) + 10 pts (KOL) = **35 pts** (before decay)  
- Combined with oracle (15 pts, 1 whale) + warden (20 pts) = 70 pts WATCHLIST  
- **Risk:** First-spike trap, organic volume not distinguished from wash trades  

**Evidence:** No check if volume is distributed or concentrated  
**Code:**
```python
volume_ratio = round(volume_1h / avg_hourly, 1) if avg_hourly > 0 else 0
# No check: is this 100 trades or 1 trade? Are buyers/sellers the same wallets?
```

### F3: Correlated False Positives (systemic)
**Location:** Signal generation across all sources  
**Issue:** All 3 sources (Oracle, Narrative, Warden) can agree on a token and still be wrong  
**Failure Mode:**  
- Sophisticated rug team:  
  - Creates token with clean Rug Warden params (liquidity, locked LP, immutable mint)  
  - Uses whale-labeled wallets to accumulate (triggers Oracle)  
  - Coordinates KOL tweets + volume spike (triggers Narrative)  
- **All signals green, conviction 85+, system auto-executes**  
- Team dumps 10 minutes later  

**Evidence:** No disconfirming signals, no negative evidence weighting  
**Current design:** Only looks for positive evidence, never asks "what would prove this wrong?"  

### F4: Partial API Failure → Unjustified Confidence (lib/heartbeat_runner.py, lines 84-95)
**Location:** Oracle and Narrative error handling  
**Issue:** If Oracle API fails, `oracle_signals = []` (empty list)  
- Narrative still runs → generates signals  
- Conviction scoring proceeds with `whales=0` (no Oracle points)  
- **Risk:** System gains confidence from Narrative alone when Oracle is down  

**Code:**
```python
except Exception as e:
    result["errors"].append(f"Oracle error: {e}")
    oracle_signals = []  # Empty signals, but heartbeat continues
```

**Correct behavior:** If Oracle fails, should flag as "partial data" and either:  
- Downgrade all conviction scores (uncertainty penalty)  
- Require higher threshold for execution (e.g., 95 instead of 85)  
- Skip entry cycle entirely (observe-only mode)  

### F5: Score Worship — No Judgment Layer (lib/scoring.py, lines 130-135)
**Location:** `ConvictionScorer.score()` recommendation logic  
**Issue:** Recommendation is purely threshold-based:  
```python
if total >= self.thresholds['auto_execute']:
    recommendation = "AUTO_EXECUTE"
```

**Missing:**  
- No check: "Are signals independent or derivative?"  
  - E.g., KOL tweet → whales follow → volume spike (1 event, 3 signals)  
- No check: "Do signals contradict each other?"  
  - E.g., Oracle shows accumulation, but Narrative age = 2min (too fast, suspicious)  
- No check: "Is this setup repeatable or a one-off anomaly?"  

**Risk:** System confuses signal quantity with signal quality  

### F6: Smart Money Mirage (lib/heartbeat_runner.py, lines 34-56)
**Location:** `parse_oracle_signals()`  
**Issue:** Counts wallets as "whales" if they appear in Nansen smart money feed  
**Missing validation:**  
- No check: What is this wallet's historical win rate?  
- No check: Is this wallet currently in profit or drawdown?  
- No check: Does this wallet dump within 30min of buying?  

**Code:**
```python
token_wallets[mint]["wallets"].add(wallet)
# Assumes all wallets in smart money feed are *consistently* smart
# Reality: some are insider traders, some are late-stage FOMO, some are dumpers
```

**Failure Mode:**  
- Wallet appears in Nansen feed because it made one good trade 6 months ago  
- Now it buys a rug token (insider info)  
- System counts it as "smart money accumulation"  
- Wallet dumps, system loses  

---

## 3. Proposed Tuning (Outcome-Focused)

**Goal:** Reduce unjustified confidence by requiring independent corroboration and failing safe under uncertainty.

### T1: Evidence Compartmentalization
**Change:** Separate "evidence collection" from "permission to trade"  
**Mechanism:** Introduce **evidence tiers**:
1. **Primary Evidence** (independent sources):  
   - Oracle whale count  
   - Narrative volume spike  
   - Rug Warden status  
2. **Secondary Evidence** (derivative/confirmatory):  
   - KOL mentions (can be triggered by whale buys)  
   - Edge Bank match (backward-looking, not predictive)  
3. **Disconfirming Evidence** (negative signals):  
   - Concentrated volume (few large trades vs many small)  
   - Whale dumper history (wallet has dumped within 30min in past)  
   - Signal age mismatch (Oracle shows accumulation, but Narrative <5min old)  

**Scoring changes:**
- PRIMARY signals contribute to score normally  
- SECONDARY signals contribute at 50% weight if no primary signal present  
- DISCONFIRMING signals subtract from total (negative weighting)  

**Example:**
- Oracle: 3 whales (40 pts PRIMARY)  
- Narrative: 10x volume, KOL mention (25 + 5 pts, KOL is SECONDARY)  
- Disconfirm: Volume is 2 large trades (−10 pts)  
- Total: 40 + 25 + 5 − 10 = **60 pts** (WATCHLIST, not AUTO_EXECUTE)  

**File:** `lib/scoring.py`  
**Lines:** 101-135 (refactor `score()` method)  

---

### T2: Explicit Disagreement Handling
**Change:** Detect when signals contradict and downgrade confidence  
**Mechanism:** Define **disagreement patterns**:
1. **Time mismatch:** Oracle accumulation detected, but Narrative age <5min  
   → Flag as "too fast, suspicious"  
2. **Volume-holder mismatch:** High volume spike, but holder count unchanged  
   → Flag as "wash trading candidate"  
3. **Oracle-narrative divergence:** Whales buying, but no social momentum  
   → Flag as "insider accumulation, not public yet"  

**Action on disagreement:**
- Downgrade recommendation by 1 tier (AUTO_EXECUTE → WATCHLIST, WATCHLIST → DISCARD)  
- Log disagreement reason for post-trade autopsy  

**File:** `lib/heartbeat_runner.py`  
**Lines:** 151-195 (insert disagreement checks before scoring)  

---

### T3: Negative-Signal Weighting
**Change:** Actively search for disconfirming evidence and penalize score  
**Mechanism:** Add **red flag checks**:
1. **Concentrated volume:** If top 3 trades account for >70% of 1h volume → −15 pts  
2. **Dumper wallets:** If any whale in Oracle has dumped a token within 30min of buying in past 7 days → −20 pts  
3. **Suspiciously clean:** If Rug Warden passes ALL checks with perfect scores → −5 pts (too good to be true)  
4. **Narrative decay:** If narrative age >45min → −10 pts (already late to the party)  

**Data sources:**
- Birdeye: top trades API (check volume concentration)  
- Nansen: wallet transaction history (check dumper pattern)  
- Narrative tracker: age since first detection  

**File:** `lib/scoring.py`  
**Lines:** Add new method `score_red_flags()`, call in `score()` before totaling  

---

### T4: Partial Data Uncertainty Penalty
**Change:** If any API fails, apply uncertainty discount to all conviction scores  
**Mechanism:**
1. Track which sources succeeded vs failed in heartbeat cycle  
2. If Oracle fails → apply 0.7x multiplier to final score  
3. If Narrative fails → apply 0.8x multiplier  
4. If Rug Warden fails → instant VETO (already implemented)  
5. If multiple sources fail → observe-only mode (no entries, watchdog only)  

**Example:**
- Oracle API timeout → `oracle_signals = []`, `oracle_failed = True`  
- Narrative detects signal → 60 pts  
- Apply 0.7x penalty → **42 pts** (DISCARD)  

**File:** `lib/heartbeat_runner.py`  
**Lines:** 84-150 (track failures, pass to scorer as `data_completeness` parameter)  
**File:** `lib/scoring.py`  
**Lines:** Add `data_completeness: float = 1.0` param to `score()`, multiply total by this factor  

---

### T5: Falsifier Pathways (Veto Expansion)
**Change:** Expand veto conditions beyond Rug Warden FAIL  
**Mechanism:** Define **instant veto triggers**:
1. Rug Warden FAIL (already implemented)  
2. All whales in Oracle are known dumpers (>2 dumps in past 7 days)  
3. Token created <2min ago (not enough time for organic discovery)  
4. Volume spike but zero social mentions (suspicious, likely wash trading)  
5. Liquidity drop >20% during signal detection (exit scam in progress)  

**Action:** Return `recommendation="VETO"` with specific reason  

**File:** `lib/scoring.py`  
**Lines:** 101-110 (add veto checks before signal scoring)  

---

### T6: Decoupling Ordering from Permission
**Change:** Separate "what looks interesting" from "what we're allowed to trade"  
**Current:** Conviction score determines both watchlist eligibility and execution permission  
**Proposed:**
- **Ordering score:** Pure signal strength (no red flags, no uncertainty penalty)  
- **Permission score:** Ordering score − red flags − uncertainty − disagreement penalties  
- **Action:** Use permission score for recommendation, log ordering score for pattern learning  

**Benefit:**  
- Can track "this setup scored 90 on ordering, but vetoed due to dumper wallets"  
- Learns which high-conviction setups are dangerous  
- Edge Bank can learn "ignore signals with dumper wallets" over time  

**File:** `lib/scoring.py`  
**Lines:** Return both `ordering_score` and `permission_score` in `ConvictionScore` dataclass  
**File:** `lib/heartbeat_runner.py`  
**Lines:** Use `permission_score` for recommendation, log `ordering_score` in bead  

---

## 4. Execution Plan (Bounded, Testable)

### Phase 1: Negative Weighting (Small Code Patch)
**Scope:** Add red flag checks, implement negative scoring  
**Files:**
- `lib/scoring.py`: Add `score_red_flags()` method  
- `lib/heartbeat_runner.py`: Pass volume concentration, wallet history to scorer  
- `lib/clients/birdeye.py`: Add `get_top_trades()` method (volume concentration check)  
- `lib/clients/nansen.py`: Add `get_wallet_history()` method (dumper detection)  

**Insertion points:**
1. `lib/scoring.py` line 135: Add red flag scoring before totaling  
2. `lib/heartbeat_runner.py` line 165: Fetch dumper history for whales  
3. `lib/heartbeat_runner.py` line 175: Fetch volume concentration for narrative tokens  

**Data structures:**
```python
@dataclass
class RedFlags:
    concentrated_volume: bool = False
    dumper_wallets: int = 0  # Count of whales with dump history
    narrative_stale: bool = False
    suspiciously_clean: bool = False
```

**Acceptance criteria:**
- If volume concentration >70% → conviction score reduced by ≥10 pts  
- If 2+ whales are dumpers → recommendation downgraded or vetoed  
- Gate 6 dry-run cycles: log red flags detected, verify no false negatives  

**Estimated effort:** 2-4 hours (coding + testing)  

---

### Phase 2: Disagreement Detection (Multi-File Refactor)
**Scope:** Add disagreement checks between Oracle, Narrative, Rug Warden  
**Files:**
- `lib/heartbeat_runner.py`: Insert disagreement checks before scoring  
- `lib/scoring.py`: Add `disagreement_penalty` parameter  
- `lib/utils/signal_validator.py`: New module for disagreement logic  

**Insertion points:**
1. `lib/heartbeat_runner.py` line 180: After signal merging, before scoring  
2. `lib/utils/signal_validator.py`: New file, define `detect_disagreements(oracle, narrative, warden)`  

**Data structures:**
```python
@dataclass
class Disagreement:
    kind: str  # "time_mismatch", "volume_holder_mismatch", "oracle_narrative_divergence"
    severity: int  # 1-3 (1=minor, 3=critical)
    penalty_pts: int  # Points to subtract
```

**Acceptance criteria:**
- Time mismatch (Oracle + Narrative <5min) → downgrade by 1 tier  
- Volume-holder mismatch → flag as wash trading, reduce score by 15 pts  
- Run Gate 6 with injected disagreement scenarios, verify penalties applied  

**Estimated effort:** 4-6 hours (new module + integration + testing)  

---

### Phase 3: Partial Data Penalty (Configuration-Only + Small Patch)
**Scope:** Apply uncertainty discount when APIs fail  
**Files:**
- `config/risk.yaml`: Add `data_completeness.penalties` section  
- `lib/heartbeat_runner.py`: Track failures, pass to scorer  
- `lib/scoring.py`: Multiply final score by completeness factor  

**Insertion points:**
1. `config/risk.yaml` line 60: Add new section  
2. `lib/heartbeat_runner.py` line 90: Track Oracle failure  
3. `lib/heartbeat_runner.py` line 120: Track Narrative failure  
4. `lib/scoring.py` line 140: Multiply `total` by `data_completeness`  

**Data structures:**
```yaml
# config/risk.yaml
data_completeness:
  penalties:
    oracle_missing: 0.7   # 30% penalty if Oracle fails
    narrative_missing: 0.8  # 20% penalty if Narrative fails
    multiple_missing: 0.5  # 50% penalty if 2+ sources fail
  observe_only_threshold: 2  # If 2+ sources fail → observe-only mode
```

**Acceptance criteria:**
- Oracle failure → final score multiplied by 0.7  
- Multiple failures → observe-only mode, no new entries  
- Gate 6 chaos injection: verify penalty applied on API 500/timeout  

**Estimated effort:** 2-3 hours (config + small patch + testing)  

---

### Phase 4: Veto Expansion (Small Code Patch)
**Scope:** Add 4 new veto conditions beyond Rug Warden FAIL  
**Files:**
- `lib/scoring.py`: Add veto checks at start of `score()`  

**Insertion points:**
1. `lib/scoring.py` line 102: After Rug Warden veto, before signal scoring  

**Veto conditions:**
```python
# Veto 1: All whales are dumpers
if signals.smart_money_whales > 0 and dumper_count == signals.smart_money_whales:
    return veto("All whales are known dumpers")

# Veto 2: Token too new
if token_age_seconds < 120:
    return veto("Token created <2min ago")

# Veto 3: Volume spike with zero social
if signals.narrative_volume_spike >= 10.0 and signals.narrative_kol_detected == False and x_mentions < 5:
    return veto("Volume spike with no social activity (wash trading)")

# Veto 4: Liquidity dropping during detection
if current_liquidity < entry_liquidity * 0.8:
    return veto("Liquidity dropped >20% during signal detection")
```

**Acceptance criteria:**
- Each veto condition tested with synthetic data  
- Gate 6 dry-run: inject veto scenarios, verify VETO recommendation  

**Estimated effort:** 2-3 hours (coding + testing)  

---

### Phase 5: Ordering vs Permission Decoupling (Multi-File Refactor)
**Scope:** Split conviction score into ordering + permission scores  
**Files:**
- `lib/scoring.py`: Refactor `ConvictionScore` dataclass, return both scores  
- `lib/heartbeat_runner.py`: Use permission score for recommendation, log ordering score  
- `lib/skills/bead_write.py`: Log both scores in autopsy bead  

**Insertion points:**
1. `lib/scoring.py` line 10: Update `ConvictionScore` dataclass  
2. `lib/scoring.py` line 140: Calculate both scores before return  
3. `lib/heartbeat_runner.py` line 190: Use `permission_score` for recommendation  
4. `lib/skills/bead_write.py`: Add `ordering_score` field  

**Data structures:**
```python
@dataclass
class ConvictionScore:
    ordering_score: int        # Pure signal strength
    permission_score: int      # After penalties
    breakdown: Dict[str, int]
    red_flags: Dict[str, int]  # Negative contributions
    disagreements: List[str]
    recommendation: str
    position_size_sol: float
    reasoning: str
```

**Acceptance criteria:**
- Ordering score always ≥ permission score  
- Beads log both scores for pattern learning  
- Gate 6 dry-run: verify permission score used for recommendations  

**Estimated effort:** 3-4 hours (refactor + integration + testing)  

---

### Execution Order
1. Phase 1 (negative weighting) → immediate value, low risk  
2. Phase 3 (partial data penalty) → complements Phase 1, quick win  
3. Phase 4 (veto expansion) → builds on existing veto logic  
4. Phase 2 (disagreement detection) → requires more integration  
5. Phase 5 (ordering/permission split) → foundational for long-term learning  

**Total estimated effort:** 13-20 hours  
**Incremental rollout:** Each phase is independently testable via Gate 6 dry-run cycles  

---

## 5. Acceptance Gates (How We Know It Worked)

### Gate 7: Red Flag Sensitivity
**Test:** Run 10 dry-run cycles with synthetic tokens injected:
1. High conviction setup (90 pts) + concentrated volume (2 trades = 80% of volume)  
2. High conviction setup (85 pts) + 2/3 whales are known dumpers  
3. High conviction setup (88 pts) + narrative age 50min (stale)  

**Pass criteria:**
- Test 1: Score reduced to <85, recommendation WATCHLIST  
- Test 2: Recommendation VETO or score <60  
- Test 3: Score reduced by 10 pts, recommendation WATCHLIST  

**Validation:** Check `red_flags` field in output JSON  

---

### Gate 8: Disagreement Downgrade
**Test:** Run 5 dry-run cycles with disagreement scenarios:
1. Oracle shows 3 whales, Narrative age <3min (time mismatch)  
2. Narrative 15x volume spike, holder count unchanged (volume-holder mismatch)  
3. Oracle accumulation, zero X mentions (oracle-narrative divergence)  

**Pass criteria:**
- Test 1: Recommendation downgraded by 1 tier or flagged as "suspicious"  
- Test 2: Score reduced by ≥15 pts, flagged as "wash trading candidate"  
- Test 3: Score reduced or flagged as "insider accumulation"  

**Validation:** Check `disagreements` list in output JSON  

---

### Gate 9: Partial Data Degradation
**Test:** Run 10 dry-run cycles with API failures:
1. Oracle timeout, Narrative succeeds (score 70 pts)  
2. Narrative 500 error, Oracle succeeds (score 50 pts)  
3. Both Oracle + Narrative fail  

**Pass criteria:**
- Test 1: Final score 70 × 0.7 = **49 pts** (DISCARD)  
- Test 2: Final score 50 × 0.8 = **40 pts** (DISCARD)  
- Test 3: Observe-only mode, no new entries  

**Validation:** Check `data_completeness` factor and `observe_only` flag  

---

### Gate 10: Veto Expansion Coverage
**Test:** Run 5 dry-run cycles with veto triggers:
1. Rug Warden FAIL (existing)  
2. Token age <2min  
3. All whales are dumpers  
4. Volume spike (10x) + zero social mentions  
5. Liquidity drop >20% during detection  

**Pass criteria:**
- All 5 tests return `recommendation="VETO"`  
- Veto reason matches trigger condition  

**Validation:** Check `recommendation` and `reasoning` fields  

---

### Gate 11: Ordering vs Permission Split
**Test:** Run 10 dry-run cycles, compare ordering vs permission scores:
1. Clean setup (no red flags) → ordering = permission  
2. Setup with 1 red flag → permission < ordering by ≥10 pts  
3. Setup with disagreement → permission < ordering  

**Pass criteria:**
- Test 1: `ordering_score == permission_score`  
- Test 2: `permission_score <= ordering_score - 10`  
- Test 3: `permission_score < ordering_score`  

**Validation:** Log both scores, verify permission score used for recommendation  

---

### Observable Behavior Improvements

**Before tuning:**
- Single-source (Oracle or Narrative) can push score to AUTO_EXECUTE  
- API failures don't reduce confidence  
- No negative evidence considered  
- Score is pure addition, no judgment layer  

**After tuning:**
- Require independent corroboration (2+ primary sources) for AUTO_EXECUTE  
- API failures trigger uncertainty penalty → lower scores  
- Red flags subtract from score → reduce unjustified confidence  
- Disagreements downgrade recommendations → safer under contradictory data  
- Veto conditions expanded → block more failure modes  

**Operator trustworthiness metric:**  
Run 100 dry-run cycles with real market data. Measure:
- **False positive rate:** % of AUTO_EXECUTE recommendations that would've been rugs  
  - Target: <10% (down from estimated 30-40% before tuning)  
- **Veto precision:** % of VETOs that were correct (token dumped within 1h)  
  - Target: >70%  
- **Partial data safety:** % of cycles with API failures that avoided bad trades  
  - Target: >90% (should enter observe-only or downgrade scores)  

---

## Summary

**Current State:**  
System is competent at signal aggregation but vulnerable to:
- Single-source domination  
- Narrative-led bias  
- Correlated false positives  
- Unjustified confidence under partial data  

**Tuning Goal:**  
Reduce false positives by requiring independent corroboration, penalizing red flags, and failing safe under uncertainty.

**Execution Path:**  
5 phases, 13-20 hours total, each phase independently testable via Gate 6-11 dry-run cycles.

**Success Criteria:**  
- False positive rate <10%  
- Veto precision >70%  
- Partial data safety >90%  
- System behaves like a competent operator who asks "what would prove this wrong?" before trading  

**Deliverable:** This document + tuned codebase passing Gates 7-11.

---

**End of Audit**
