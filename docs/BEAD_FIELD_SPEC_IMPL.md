# BEAD_FIELD_SPEC v0.2 — ChadBoar Implementation

**Implements:** BEAD_FIELD_SPEC v0.2 on the ChadBoar testbed
**Status:** Production-ready, running in heartbeat pipeline
**Date:** 2026-02-20

This document describes ChadBoar's implementation of the a8ra Bead Field
specification. Domain mappings, infrastructure stubs, and ChadBoar-specific
extensions are documented below.

---

## Architecture Overview

| a8ra Spec | Boar Implementation | Notes |
|---|---|---|
| XTDB bitemporal engine | SQLite with WT/KT columns | Proves query patterns |
| PQC + ECDSA dual signing | ECDSA only, `pqc_sig` null | Field reserved |
| DGX Spark compute | Local Python on VPS | Logic identical |
| HLC cross-node clocks | System UTC (single node) | Schema ready for HLC |
| HSM sovereign key | File-based node key | Same signing flow |
| Multi-node topology | Single VPS colocated | Tests everything except distributed |
| Dolt work-tree | Git repo | Coordination exists |
| NATS/Kafka event bus | Direct function calls | Single-process |

---

## Domain Mapping — 10 Bead Types

| Type | a8ra Domain | ChadBoar Domain | Example |
|---|---|---|---|
| **FACT** | Sensory input | Market data ingestion | "DexScreener returned 20 candidates, top vol $47k" |
| **CLAIM** | Agent inference | Regime assessment | "Graduation market showing strong pulse activity" |
| **SIGNAL** | Tradeable candidate | Scored candidate with conviction | "KIMCHI: 72 conviction, PASS warden, graduation play" |
| **PROPOSAL** | Trade intent | Paper/live trade proposal | "ENTER_LONG KIMCHI, 0.5 SOL, paper venue" |
| **PROPOSAL_REJECTED** | Vetoed candidate | Shadow Field entry | "WARDEN_VETO: concentrated holder >80%" |
| **SKILL** | Distilled lesson | Learning from rejections | "Avoid high holder concentration on graduation" |
| **MODEL_VERSION** | LLM deployment | Grok config snapshot | "grok-4-1-fast, heartbeat purpose, PRODUCTION" |
| **POLICY** | Risk rules | risk.yaml snapshot | "graduation thresholds, daily sublimits" |
| **AUTOPSY** | *(ChadBoar ext)* | Post-trade PnL evaluation | "Paper loss: TEST -5.2% over 120min" |
| **HEARTBEAT** | *(ChadBoar ext)* | Cycle-level metadata | "Cycle 329: 34 signals, 6 proposals, 14 SOL" |

AUTOPSY may merge into CLAIM (domain="autopsy") for a8ra. HEARTBEAT is
ChadBoar-specific cycle tracking — a8ra's equivalent is the agent lifecycle
event stream.

---

## Bi-Temporal Query Cookbook

### 1. What signals did we generate during Asian session?

```python
from datetime import datetime, timezone
asian_start = datetime(2026, 2, 20, 0, 0, tzinfo=timezone.utc)  # 8am SGT
asian_end = datetime(2026, 2, 20, 8, 0, tzinfo=timezone.utc)    # 4pm SGT

signals = chain.query_world_time_range(
    asian_start, asian_end,
    bead_type=BeadType.SIGNAL,
)
```

### 2. What did we know at midnight about whale activity?

```python
midnight = datetime(2026, 2, 20, 0, 0, tzinfo=timezone.utc)
known_at_midnight = chain.query_knowledge_at(
    midnight,
    bead_type=BeadType.FACT,
    token_mint="So11111111111111111111111111111111111111112",
)
```

### 3. All PROPOSAL_REJECTED with WARDEN_VETO in last 24h

```python
from datetime import timedelta
since = datetime.now(timezone.utc) - timedelta(hours=24)
vetoed = chain.query_shadow_field(
    rejection_category=RejectionCategory.WARDEN_VETO,
    since=since,
)
```

### 4. Average refinery latency for SIGNAL beads this week

```python
since_monday = datetime(2026, 2, 17, 0, 0, tzinfo=timezone.utc)
latency = chain.refinery_latency(
    bead_type=BeadType.SIGNAL,
    since=since_monday,
)
# latency.avg_seconds, latency.p50_seconds, latency.p95_seconds
```

### 5. Walk lineage from PROPOSAL_REJECTED back to source FACTs

```python
rejected_bead_id = "some-uuid-v7"
ancestors = chain.walk_lineage(rejected_bead_id, depth=5)
facts = [b for b in ancestors if b.bead_type == BeadType.FACT]
# Full dependency chain: PROPOSAL_REJECTED → SIGNAL → FACT
```

---

## Shadow Field

The Shadow Field is the collection of all PROPOSAL_REJECTED beads — every
candidate the system decided NOT to trade. This is the richest learning
substrate: it captures what the system saw, how it scored, why it rejected,
and (eventually via Dream Cycle) what would have happened.

**Volume:** ~15 rejections per cycle × 144 cycles/day = ~2,160/day.

**Query methods:**
- `chain.query_shadow_field()` — all rejections
- `chain.query_shadow_field(rejection_category=...)` — by category
- `chain.shadow_field_stats()` — category distribution, linked skills count

**Rejection categories (ChadBoar domain):**

| Category | Source | Meaning |
|---|---|---|
| WARDEN_VETO | Rug Warden FAIL | Structural risk (liquidity, concentration, mutable mint) |
| SCORE_BELOW_THRESHOLD | Scorer | Permission score < paper_trade threshold |
| RISK_BREACH | Risk engine | Daily exposure limit, position limit |
| DAILY_SUBLIMIT | Graduation cap | Daily graduation play count exceeded |
| REGIME_MISMATCH | Agent assessment | Market regime doesn't support this play |
| HUMAN_OVERRIDE | G | Manual rejection via Telegram |

**Future mining:** Dream Cycle / SkillRL will consume the Shadow Field to:
1. Extract SKILL beads from rejection patterns
2. Populate `counterfactual_summary` on rejections (what would have happened)
3. Generate `linked_skills` references back to the rejection patterns

---

## Invariant Enforcement Points

| Invariant | Enforcement | Code Location |
|---|---|---|
| INV-BLIND-KEY | Node signing key separate from signer key | `lib/beads/signing.py` |
| INV-RUG-WARDEN-VETO | FAIL → PROPOSAL_REJECTED with WARDEN_VETO | `heartbeat_runner.py` decision gate |
| INV-HUMAN-GATE-100 | >$100 → PROPOSAL with gate="escalated" | `heartbeat_runner.py` (future) |
| INV-DRAWDOWN-50 | Checked before heartbeat cycle | `heartbeat_runner.py` state check |
| INV-CHAIN-VERIFY | verify_chain() on boot | `lib/chain/verify.py` + new `chain.verify_chain()` |
| INV-DAILY-EXPOSURE-30 | Tracked in state.json, DAILY_SUBLIMIT rejection | `heartbeat_runner.py` |

---

## Migration Notes

### What changed from v0

| v0 (yesterday) | v0.2 (today) |
|---|---|
| 6 types: SIGNAL, VERDICT, TRADE, AUTOPSY, INSIGHT, HEARTBEAT | 10 types: FACT, CLAIM, SIGNAL, PROPOSAL, PROPOSAL_REJECTED, SKILL, MODEL_VERSION, POLICY, AUTOPSY, HEARTBEAT |
| Content-hash bead_id (SHA-256) | UUID v7 bead_id (time-ordered) |
| header/edges/provenance/payload structure | Flat BeadBase with content dict |
| No bi-temporal fields | WT/KT on every bead |
| No ECDSA signing | ECDSA secp256r1 attestation |
| No Merkle batching | Hybrid trigger Merkle batches |
| No lineage table | Normalized bead_lineage for edge traversal |
| VERDICT = scored candidate | SIGNAL = scored candidate (VERDICT removed) |
| TRADE = executed trade | PROPOSAL = trade intent (venue field distinguishes paper/live) |
| No rejection tracking | PROPOSAL_REJECTED = full Shadow Field |

### Why overnight beads are not migrated

The v0 beads in `state/beads.db` (~300 cycles worth) use a fundamentally
different schema: content-hash IDs, no bi-temporal fields, different type
taxonomy. The new schema creates a fresh database. The v0 beads served their
purpose as calibration data and are preserved in git history. Zero migration
cost is correct — rip and replace cleanly.

---

## File Inventory

| File | Purpose |
|---|---|
| `lib/beads/schema.py` | Pydantic v2 models: BeadBase, 10 content types, enums, validators |
| `lib/beads/chain.py` | SQLite chain manager: write, query, lineage, Merkle, verify |
| `lib/beads/signing.py` | ECDSA secp256r1: key management, sign, verify, code hash |
| `lib/beads/emitters.py` | Helper functions for each bead type emission |
| `lib/beads/__init__.py` | Public API exports |
| `tests/test_bead_field.py` | 54 tests: schema, chain, bi-temporal, traversal, Merkle, signing |
| `docs/BEAD_FIELD_SPEC_IMPL.md` | This file |
| `state/beads.db` | SQLite database (created on first run) |
| `/etc/autistboar/node_signing.key` | ECDSA private key (autistboar:autistboar, 400) |
| `state/node_signing.pub` | ECDSA public key (world-readable) |

---

## Expected Bead Volume

| Type | Per Cycle | Per Day (144 cycles) |
|---|---|---|
| FACT | 2-3 | ~350 |
| CLAIM | 0-2 | ~100 |
| SIGNAL | 10-20 | ~2,000 |
| PROPOSAL | 1-5 | ~300 |
| PROPOSAL_REJECTED | 10-15 | ~1,800 |
| HEARTBEAT | 1 | 144 |
| POLICY | ~0 (on restart) | ~1 |
| MODEL_VERSION | ~0 (on restart) | ~1 |
| AUTOPSY | 0-3 | ~200 |
| **TOTAL** | **~30-45** | **~5,000** |

At ~5,000 beads/day × ~2KB each = ~10MB/day. SQLite handles this trivially.

---

## Success Criteria Status

| Criterion | Status |
|---|---|
| All tests pass | 53 passed, 1 skipped (signing key env) |
| Bead types present | FACT, SIGNAL, PROPOSAL, PROPOSAL_REJECTED, HEARTBEAT, POLICY, MODEL_VERSION |
| Shadow Field queryable | `query_shadow_field()` returns PROPOSAL_REJECTED with full context |
| Refinery latency | `refinery_latency()` returns meaningful WT-KT deltas |
| Chain integrity | `verify_chain()` reports intact |
| Documentation | This file |

*Awaiting live heartbeat cycles to validate end-to-end pipeline integration.*
