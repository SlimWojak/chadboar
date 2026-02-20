# COO BRIEF — BEAD_FIELD_SPEC Implementation for ChadBoar

**From:** CTO (Claude Desktop)
**To:** COO (Claude Code, VPS)
**Date:** 2026-02-20
**Priority:** High — this is foundational infrastructure
**Estimated scope:** Large (multiple files, full schema replacement, tests, docs)
**Permission:** `--dangerously-skip-permissions` approved

---

## Context

ChadBoar is a testbed for a8ra's core autonomous agent architecture. G has authored a comprehensive Bead Field Specification (BEAD_FIELD_SPEC v0.2) that defines the data substrate for the full a8ra system. Your job is to implement this spec on ChadBoar — not a dumbed-down version, but the real thing adapted to the memecoin trading domain.

The bead schema you built yesterday (~12 hours ago, `lib/beads/schema.py` and `lib/beads/chain.py`) was excellent v0 work. We're now replacing it with the production-grade spec. Zero migration cost — the overnight beads are reference data, not precious. Rip and replace cleanly.

**Why full spec on a single-VPS memecoin bot?** ChadBoar is the canary. We validate the logic, find the edge cases, and prove the lifecycle works under real autonomous operation before touching a8ra's core system. Every janky edge discovered here saves weeks in production.

---

## Architecture Decisions (already made — implement, don't debate)

| a8ra Spec | Boar Implementation | Rationale |
|---|---|---|
| XTDB bitemporal engine | SQLite with explicit WT/KT columns | Proves query patterns without engine dependency |
| PQC + ECDSA dual signing | ECDSA only, `pqc_sig` field present but null | PQC libs immature, field reserved for future |
| DGX Spark compute | Local Python on VPS | Logic identical, just slower |
| HLC cross-node clocks | System UTC clock (single node) | No skew on single node, schema ready for HLC |
| HSM sovereign key | File-based key (Blind KeyMan pattern) | Same signing flow, different key storage |
| Multi-node topology | Single VPS, all services colocated | Tests everything except distributed coordination |
| Dolt work-tree | Git repo (existing) | Coordination layer already exists |
| NATS/Kafka event bus | Direct function calls | Single-process, no bus needed |

---

## Deliverables

### 1. Bead Schema (`lib/beads/schema.py`) — Full Replacement

Replace the current schema with the BEAD_FIELD_SPEC v0.2 schema. Pydantic v2 strict mode throughout.

#### 1.1 Universal Base (all beads share this)

```python
class BeadBase(BaseModel):
    # Identity
    bead_id: str  # UUID v7 (time-ordered). Generate at creation, NOT content-hash.
    bead_type: BeadType  # enum: FACT, CLAIM, SIGNAL, PROPOSAL, PROPOSAL_REJECTED, SKILL, MODEL_VERSION, POLICY

    # Bi-Temporal Fields
    world_time_valid_from: datetime | None = None  # Start of observation window in external reality
    world_time_valid_to: datetime | None = None     # End of observation window
    knowledge_time_recorded_at: datetime            # Moment of commitment (always required)
    temporal_class: TemporalClass                   # enum: OBSERVATION, PATTERN, DERIVED

    # Provenance
    source_ref: SourceRef  # {source_type, source_id, source_version}
    lineage: list[str]     # Ordered bead_ids this derives from. Empty for root beads.

    # Integrity
    hash_self: str         # SHA-256 of canonical content (everything except hash_self, merkle_batch_id)
    hash_prev: str | None = None  # Previous bead in this stream (per-stream chain)
    merkle_batch_id: str | None = None  # Set when batch anchor occurs

    # Attestation (Layer 3)
    attestation: AttestationEnvelope  # {air_node_id, code_hash, model_hash, ecdsa_sig, pqc_sig: null}

    # Operational
    status: BeadStatus = BeadStatus.ACTIVE  # ACTIVE, SUPERSEDED, RETRACTED
    superseded_by: str | None = None
    retraction_reason: str | None = None
    tags: list[str] = []

    # Content (type-specific, defined per subclass)
    content: dict  # Structured payload
```

#### 1.2 Enums

```python
class BeadType(str, Enum):
    FACT = "FACT"
    CLAIM = "CLAIM"
    SIGNAL = "SIGNAL"
    PROPOSAL = "PROPOSAL"
    PROPOSAL_REJECTED = "PROPOSAL_REJECTED"
    SKILL = "SKILL"
    MODEL_VERSION = "MODEL_VERSION"
    POLICY = "POLICY"

class TemporalClass(str, Enum):
    OBSERVATION = "OBSERVATION"    # Tied to specific market time
    PATTERN = "PATTERN"            # Timeless methodology
    DERIVED = "DERIVED"            # Computed from other beads

class BeadStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    RETRACTED = "RETRACTED"

class SourceType(str, Enum):
    MARKET_DATA = "MARKET_DATA"
    AGENT = "AGENT"
    HUMAN = "HUMAN"
    EXTRACTION = "EXTRACTION"
    SIMULATION = "SIMULATION"

class RejectionCategory(str, Enum):
    PROVENANCE_FAILURE = "PROVENANCE_FAILURE"
    LOGICAL_CONTRADICTION = "LOGICAL_CONTRADICTION"
    REGIME_MISMATCH = "REGIME_MISMATCH"
    RISK_BREACH = "RISK_BREACH"
    STALE_DATA = "STALE_DATA"
    FALSIFICATION_FAILED = "FALSIFICATION_FAILED"
    HUMAN_OVERRIDE = "HUMAN_OVERRIDE"
    WARDEN_VETO = "WARDEN_VETO"  # ChadBoar-specific: Rug Warden hard fail
    DAILY_SUBLIMIT = "DAILY_SUBLIMIT"  # ChadBoar-specific: graduation cap hit
    SCORE_BELOW_THRESHOLD = "SCORE_BELOW_THRESHOLD"  # ChadBoar-specific
```

#### 1.3 Supporting Models

```python
class SourceRef(BaseModel):
    source_type: SourceType
    source_id: str       # agent ID, data provider name, human identifier
    source_version: str | None = None  # code hash, model version, null for human

class AttestationEnvelope(BaseModel):
    air_node_id: str            # "chadboar-vps-sg1" (physical node identifier)
    code_hash: str              # Git commit hash of executing code
    model_hash: str | None = None  # Model version hash if LLM involved
    container_hash: str | None = None  # Not used on Boar (no containers)
    ecdsa_sig: str              # ECDSA secp256r1 signature over hash_self
    pqc_sig: str | None = None  # Reserved for future PQC (Dilithium). Always null for now.
```

#### 1.4 Type-Specific Content Models

Map the a8ra types to ChadBoar's memecoin trading domain:

**FACT Content** — Market data ingestion
```python
class FactContent(BaseModel):
    symbol: str              # Token symbol or "SOL" or "MARKET"
    token_mint: str | None = None  # Solana address if token-specific
    field: str               # "price_fdv", "volume_24h", "liquidity", "holder_count", "whale_flow"
    value: float | str | dict  # The actual data point
    as_of_world_time: datetime  # Precise market timestamp
    provider: str            # "dexscreener", "nansen", "mobula", "birdeye", "helius"
    quality_score: float | None = None  # Provider-reported confidence, NOT agent judgment
```

**CLAIM Content** — Agent's intermediate inference
```python
class ClaimContent(BaseModel):
    conclusion: str          # "Graduation market showing strong pulse activity"
    reasoning_trace: str     # How the agent reached this conclusion
    premises_ref: list[str]  # bead_ids of FACT/CLAIM beads this derives from
    confidence_basis: str    # Qualitative basis, NOT numeric score
    domain: str              # "pulse_quality", "whale_activity", "narrative_momentum", "regime"
    tokens_referenced: list[str] = []  # Token mints discussed
```

**SIGNAL Content** — Tradeable candidate
```python
class SignalContent(BaseModel):
    token_mint: str
    token_symbol: str
    play_type: str           # "graduation" or "accumulation"
    direction: str = "LONG"  # Always long for memecoin scalps
    discovery_source: str    # "pulse-bonding", "nansen-dex-trades", "dexscreener-volume"
    scoring_breakdown: dict  # Full weighted scores per component
    conviction_score: int    # 0-100
    warden_verdict: str      # "PASS", "WARN", "FAIL"
    red_flags: dict = {}
    raw_metrics: dict = {}   # volume, FDV, liquidity, holder_count, etc.
    risk_profile: dict = {}  # invalidation conditions
    supporting_claims: list[str] = []  # CLAIM bead_ids
    supporting_facts: list[str] = []   # FACT bead_ids
```

**PROPOSAL Content** — Trade intent
```python
class ProposalContent(BaseModel):
    signal_ref: str          # bead_id of the SIGNAL this executes
    action: str              # "ENTER_LONG", "EXIT"
    token_mint: str
    token_symbol: str
    entry_price_fdv: float | None = None
    position_size_sol: float | None = None
    position_size_method: str = "score_weighted"  # from risk.yaml formula
    stop_loss: dict | None = None   # exit tiers from risk.yaml
    constraints: list[str] = []     # ["daily_limit_not_exceeded", "warden_pass"]
    execution_venue: str = "solana_mainnet"  # or "paper"
    gate: str               # "auto", "escalated", "human_approved"
```

**PROPOSAL_REJECTED Content** — Full proposal + rejection context
```python
class ProposalRejectedContent(BaseModel):
    # --- FULL PROPOSAL SNAPSHOT (identical to ProposalContent) ---
    signal_ref: str
    action: str
    token_mint: str
    token_symbol: str
    entry_price_fdv: float | None = None
    position_size_sol: float | None = None
    position_size_method: str = "score_weighted"
    stop_loss: dict | None = None
    constraints: list[str] = []
    execution_venue: str = "solana_mainnet"
    gate: str

    # --- REJECTION CONTEXT ---
    rejection_source: str     # "rug_warden", "risk_engine", "scoring", "daily_sublimit", "human"
    rejection_reason: str     # Specific, structured reason
    rejection_category: RejectionCategory
    rejection_policy_ref: str | None = None  # bead_id of POLICY bead active at rejection time
    scoring_breakdown_at_rejection: dict = {}  # Full score state when rejected
    warden_detail: dict | None = None  # Which warden check failed and why
    risk_metrics_at_rejection: dict = {}  # pot_sol, daily_exposure, etc.
    counterfactual_summary: str | None = None  # Populated later by Dream Cycle
    linked_skills: list[str] | None = None     # SKILL bead_ids generated from this
```

**SKILL Content** — Distilled lesson
```python
class SkillContent(BaseModel):
    skill_name: str          # "avoid_high_holder_concentration_graduation"
    skill_type: str          # "AVOIDANCE", "RECOGNITION", "TIMING", "SIZING", "REGIME"
    description: str         # What this skill teaches
    failure_trajectory_refs: list[str]  # PROPOSAL_REJECTED bead_ids
    success_trajectory_refs: list[str] = []  # PROPOSAL bead_ids where skill helped
    conditions: dict         # Structured IF-THEN
    distillation_method: str  # "manual", "dream_cycle_v1"
    validation_status: str = "CANDIDATE"  # CANDIDATE, VALIDATED, PROMOTED, DEPRECATED
    validated_by: str | None = None
```

**MODEL_VERSION Content**
```python
class ModelVersionContent(BaseModel):
    model_name: str          # "grok-4-1-fast", "claude-sonnet-4.5"
    version_hash: str        # Model identifier from provider
    purpose: str             # "heartbeat", "chat", "escalation"
    deployment_status: str   # "PRODUCTION", "RETIRED"
    config_snapshot: dict = {}  # Relevant config at deployment
```

**POLICY Content**
```python
class PolicyContent(BaseModel):
    policy_name: str         # "graduation_risk_thresholds", "daily_sublimit"
    policy_type: str         # "RISK", "EXECUTION", "REGIME", "OPERATIONAL"
    rules: dict              # Structured policy definition (mirrors risk.yaml sections)
    effective_from: datetime
    effective_to: datetime | None = None
    supersedes: str | None = None  # Previous policy bead_id
    authority: str           # "G", "system_default"
```

#### 1.5 Validation Rules (Pydantic validators)

Enforce these at model level:

1. **Temporal class consistency:**
   - `OBSERVATION` → `world_time_valid_from` and `world_time_valid_to` must both be set
   - `PATTERN` → both must be null
   - `DERIVED` → computed from lineage (can be deferred, but field must be present)

2. **PROPOSAL_REJECTED must have rejection_category and rejection_reason.** Lightweight stubs are schema violations.

3. **RISK_BREACH rejection must have `rejection_policy_ref`.** Cannot reject for risk breach without citing which policy.

4. **Lineage required for all types except root FACTs.** SIGNAL must reference at least one FACT or CLAIM. PROPOSAL must reference a SIGNAL. PROPOSAL_REJECTED must reference the SIGNAL it was rejecting.

5. **hash_self computed deterministically.** Canonical JSON serialization (sorted keys, no whitespace) of all fields except `hash_self`, `merkle_batch_id`, `hash_prev`. Same content must always produce same hash.

6. **bead_id is UUID v7.** Time-ordered, globally unique. NOT content-hash (that's `hash_self`).

---

### 2. Bead Chain Manager (`lib/beads/chain.py`) — Full Replacement

Replace current chain.py with bi-temporal-aware chain manager.

#### 2.1 SQLite Schema

```sql
CREATE TABLE beads (
    seq                      INTEGER PRIMARY KEY AUTOINCREMENT,
    bead_id                  TEXT NOT NULL UNIQUE,
    bead_type                TEXT NOT NULL,
    hash_self                TEXT NOT NULL UNIQUE,
    hash_prev                TEXT,
    merkle_batch_id          TEXT,
    
    -- Bi-temporal columns (indexed for range queries)
    world_time_valid_from    TEXT,  -- ISO 8601 or NULL
    world_time_valid_to      TEXT,  -- ISO 8601 or NULL
    knowledge_time_recorded_at TEXT NOT NULL,  -- ISO 8601
    temporal_class           TEXT NOT NULL,  -- OBSERVATION, PATTERN, DERIVED
    
    -- Denormalized for query performance
    token_mint               TEXT DEFAULT '',
    status                   TEXT NOT NULL DEFAULT 'ACTIVE',
    tags                     TEXT NOT NULL DEFAULT '[]',  -- JSON array
    
    -- Full bead data
    content                  TEXT NOT NULL,     -- JSON (type-specific payload)
    lineage                  TEXT NOT NULL,     -- JSON array of bead_ids
    source_ref               TEXT NOT NULL,     -- JSON
    attestation              TEXT NOT NULL,     -- JSON
    full_bead                TEXT NOT NULL,     -- JSON (complete serialized bead)
    
    created_at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Performance indexes
CREATE INDEX idx_beads_type ON beads(bead_type);
CREATE INDEX idx_beads_token ON beads(token_mint);
CREATE INDEX idx_beads_kt ON beads(knowledge_time_recorded_at);
CREATE INDEX idx_beads_wt_from ON beads(world_time_valid_from);
CREATE INDEX idx_beads_wt_to ON beads(world_time_valid_to);
CREATE INDEX idx_beads_temporal_class ON beads(temporal_class);
CREATE INDEX idx_beads_status ON beads(status);
CREATE INDEX idx_beads_merkle ON beads(merkle_batch_id);

-- Lineage index table (for efficient edge traversal)
CREATE TABLE bead_lineage (
    bead_id     TEXT NOT NULL,
    parent_id   TEXT NOT NULL,
    position    INTEGER NOT NULL,  -- Order in lineage array
    PRIMARY KEY (bead_id, parent_id)
);
CREATE INDEX idx_lineage_parent ON bead_lineage(parent_id);

-- Merkle batch tracking
CREATE TABLE merkle_batches (
    batch_id      TEXT PRIMARY KEY,
    merkle_root   TEXT NOT NULL,
    bead_count    INTEGER NOT NULL,
    trigger_type  TEXT NOT NULL,  -- DECISION_BOUNDARY, MAX_BEADS, MAX_TIME
    trigger_bead_id TEXT,         -- Which bead triggered (null for cap triggers)
    created_at    TEXT NOT NULL,
    anchor_tx     TEXT            -- External anchor reference (Solana tx hash, null until anchored)
);
```

#### 2.2 Chain Manager API

```python
class BeadChain:
    def __init__(self, db_path: str = "state/beads.db"):
        ...

    # --- Write ---
    def write_bead(self, bead: BeadBase) -> str:
        """Validate, compute hash_self, set hash_prev, sign, insert.
        Also inserts lineage edges into bead_lineage table.
        Returns bead_id."""

    # --- Read ---
    def get_bead(self, bead_id: str) -> BeadBase | None
    def get_chain_head(self, stream: str = "main") -> BeadBase | None

    # --- Query (all support `since` and `until` datetime filters) ---
    def query_by_type(self, bead_type: BeadType, *, limit=50, since=None, until=None) -> list[BeadBase]
    def query_by_token(self, token_mint: str, *, limit=50, since=None, until=None) -> list[BeadBase]
    def query_by_temporal_class(self, tc: TemporalClass, *, limit=50, since=None, until=None) -> list[BeadBase]
    def query_by_tag(self, tag: str, *, limit=50) -> list[BeadBase]
    def query_by_status(self, status: BeadStatus, *, limit=50) -> list[BeadBase]

    # --- Edge Traversal ---
    def get_lineage(self, bead_id: str) -> list[BeadBase]:
        """Return all beads in this bead's lineage chain (direct parents)."""
    def get_descendants(self, bead_id: str) -> list[BeadBase]:
        """Return all beads that reference this bead in their lineage."""
    def walk_lineage(self, bead_id: str, depth: int = 10) -> list[BeadBase]:
        """Recursive lineage walk — full dependency tree up to depth."""

    # --- Shadow Field ---
    def query_shadow_field(self, *, rejection_category=None, since=None, until=None, limit=100) -> list[BeadBase]:
        """All PROPOSAL_REJECTED beads, optionally filtered by category and time."""
    def shadow_field_stats(self) -> dict:
        """Rejection category distribution, volume over time, linked skills count."""

    # --- Bi-Temporal Queries ---
    def query_world_time_range(self, wt_from: datetime, wt_to: datetime, *, bead_type=None) -> list[BeadBase]:
        """What observations cover this world-time window?"""
    def query_knowledge_at(self, kt: datetime, *, bead_type=None, token_mint=None) -> list[BeadBase]:
        """What did we know at this point in time? (KT <= given time)"""
    def refinery_latency(self, *, bead_type=None, since=None) -> dict:
        """Average, p50, p95, p99 of (KT - WT_end) for OBSERVATION beads."""

    # --- Integrity ---
    def verify_chain(self, stream: str = "main") -> ChainVerifyResult
    def get_chain_stats(self) -> dict:
        """Total beads, type distribution, temporal class distribution,
        shadow field size, edge completeness, chain integrity status,
        lineage depth stats, merkle batch count."""

    # --- Merkle ---
    def check_anchor_trigger(self) -> str | None:
        """Check if anchoring should trigger. Returns trigger type or None.
        Triggers: DECISION_BOUNDARY (SIGNAL/PROPOSAL committed since last anchor),
        MAX_BEADS (500+), MAX_TIME (1h+). Whichever fires first."""
    def create_merkle_batch(self, trigger_type: str, trigger_bead_id: str = None) -> str:
        """Build Merkle tree over unanchored beads, create batch record.
        Backfill merkle_batch_id on all included beads. Return batch_id."""

    # --- Export ---
    def export_chain_jsonl(self, path: str) -> int:
        """Full chain as JSONL. Git-friendly, a8ra-compatible."""
    def import_chain_jsonl(self, path: str) -> int:
        """Import from JSONL. For migration/restore."""
```

#### 2.3 Signing Implementation

For ECDSA signing on ChadBoar:

```python
# At chain initialization, load or generate a node signing key
# Store at: /etc/autistboar/node_signing.key (similar to signer.key)
# Use: ecdsa library (pip install ecdsa)
# Sign: hash_self of each bead at write time
# Verify: on read, optionally verify ecdsa_sig against known public key
# pqc_sig: always None (reserved)
```

Generate the node signing keypair on first run if it doesn't exist. Store private key at `/etc/autistboar/node_signing.key` (chmod 400, root-owned). Public key at `state/node_signing.pub` (readable by all). The attestation envelope gets filled at write_bead() time.

For `code_hash`: use the current git commit hash (`git rev-parse HEAD`). Cache it at chain init.

---

### 3. Commitment Threshold Enforcement

Implement the formal handoff protocol. This is about WHERE beads get written in the pipeline, not a code-level `commit()` function (that's an a8ra abstraction for multi-agent). On ChadBoar, the commitment threshold is:

**Always bead (MANDATORY):**
- Every PROPOSAL (trade intent, paper or live)
- Every PROPOSAL_REJECTED (veto, discard, sublimit hit, score below threshold)
- Every SIGNAL (candidate that enters scoring — conviction > 0)
- FACTs from data pipeline (price, volume, whale flow — one per cycle per source)
- POLICY snapshots (on startup, on config change)
- MODEL_VERSION (on startup, on model change)

**Never bead:**
- Internal scoring calculations before final verdict
- API call attempts/retries
- Log messages
- Agent chain-of-thought
- Health check pings

**The critical behavior change:** Currently, Boar only writes beads for candidates that PASS scoring. Under the new spec, every candidate that enters the funnel — including all 15 vetoed per cycle — produces a SIGNAL bead, and every rejection produces a PROPOSAL_REJECTED bead. This means bead volume increases significantly (~30-40 beads per cycle instead of ~5). That's correct — the Shadow Field needs volume.

---

### 4. Integration into Heartbeat Pipeline

Wire bead emission into `lib/heartbeat_runner.py`. This is the big integration pass.

#### 4.1 Pipeline Start — POLICY + MODEL_VERSION Beads

On first heartbeat after restart (or on config change detection):
- Emit POLICY bead capturing current `risk.yaml` thresholds (graduation limits, accumulation limits, warden config, daily sublimits)
- Emit MODEL_VERSION bead for current Grok config (model name, purpose, OpenRouter endpoint)

These are temporal_class=PATTERN, world_time=null.

#### 4.2 Data Ingestion — FACT Beads

At each data source query (Nansen, DexScreener, Mobula whale scan):
- Emit one FACT bead per data source per cycle summarizing what was returned
- Example: `FACT: DexScreener returned 20 candidates, top volume $KIMCHI at $47k 24h volume`
- These are temporal_class=OBSERVATION with world_time = cycle timestamp span
- Don't emit per-token FACTs (too noisy) — emit per-source summary FACTs
- Include data_sources status in content: `{"dexscreener": "OK", "nansen": "OK", "mobula": "TIMEOUT"}`

#### 4.3 Scoring — SIGNAL + CLAIM Beads

For each candidate that enters the scoring pipeline (after initial filter, before verdict):
- Emit SIGNAL bead with full scoring breakdown, warden verdict, red flags
- `temporal_class=DERIVED`, `lineage` pointing to the FACT beads from this cycle's data sources
- Token-specific: one SIGNAL per scored candidate

If the agent makes a regime-level assessment (e.g., "graduation market is cold, pulse activity low"):
- Emit CLAIM bead with the assessment
- `temporal_class=OBSERVATION`, `domain="regime"`
- This is a conditional bead — only when the agent explicitly commits an assessment

#### 4.4 Decision Gate — PROPOSAL or PROPOSAL_REJECTED

For each SIGNAL, one of two outcomes:

**If recommendation is PAPER_TRADE, WATCHLIST, or AUTO_EXECUTE:**
- Emit PROPOSAL bead with trade intent details
- `lineage = [signal_bead_id]`
- Then execute (paper trade or live trade) and update PROPOSAL status if needed

**If recommendation is VETO or DISCARD:**
- Emit PROPOSAL_REJECTED bead with FULL signal snapshot + rejection context
- Map current rejection reasons to `RejectionCategory`:
  - Rug Warden FAIL → `WARDEN_VETO` (include which check failed)
  - Score below threshold → `SCORE_BELOW_THRESHOLD`
  - Daily sublimit hit → `DAILY_SUBLIMIT`
  - Warden WARN + low score → `RISK_BREACH` (reference active POLICY bead)
- `lineage = [signal_bead_id]`
- Include `scoring_breakdown_at_rejection` and `risk_metrics_at_rejection`

**This is the Shadow Field in action.** Every cycle will produce ~15 PROPOSAL_REJECTED beads — this is correct and desired.

#### 4.5 Trade Close — AUTOPSY (stub for now)

Paper trade PnL checks in `paper_trade.py`:
- When a trade closes (6h expiry or PnL threshold), emit AUTOPSY as a special CLAIM bead
- Wait — the spec doesn't have AUTOPSY as a type. Map it to: emit a CLAIM bead with `domain="autopsy"` containing PnL data, lesson, and supports/contradicts edges
- OR: since this is a testbed, add `AUTOPSY` as a 9th bead type (ChadBoar extension). The a8ra spec can decide later whether autopsy is a CLAIM subtype or its own type. I'd recommend keeping it as a separate type for clean Shadow Field queries.

**CTO Decision: Add AUTOPSY as a ChadBoar-specific 9th type.** Include it in the schema with a comment noting it may merge into CLAIM for a8ra. The autopsy enforcement from yesterday (must have supports or contradicts edge) carries forward.

#### 4.6 Cycle End — HEARTBEAT Bead

At cycle completion:
- Emit HEARTBEAT bead (keep as ChadBoar-specific type, not in a8ra spec)
- Content: cycle_number, signals_found, signals_vetoed, proposals, pot_sol, pipeline_health, canary_hash
- `temporal_class=OBSERVATION`, world_time = cycle start to cycle end
- `lineage = [previous_heartbeat_bead_id]`

---

### 5. Merkle Anchoring

Implement the hybrid trigger system:

1. **Decision boundary trigger:** After writing a SIGNAL or PROPOSAL bead, call `check_anchor_trigger()`. If it returns a trigger type, call `create_merkle_batch()`.

2. **Fallback triggers:** At end of each heartbeat cycle, check:
   - Beads since last anchor > 500 → trigger
   - Time since last anchor > 1 hour → trigger

3. **Merkle tree construction:** Simple binary Merkle tree over `hash_self` values of all beads in the batch. Store root in `merkle_batches` table. Backfill `merkle_batch_id` on all included beads.

4. **External anchoring (Solana):** STUB for now. The `anchor_tx` field in `merkle_batches` stays null. ChadBoar already has Solana anchoring code in the legacy chain — we can wire it later. The Merkle batch structure is the important part.

---

### 6. Documentation (`docs/BEAD_FIELD_SPEC_IMPL.md`)

Write implementation documentation covering:

1. **Relationship to canonical spec:** "This implements BEAD_FIELD_SPEC v0.2 on the ChadBoar testbed. Domain mappings, infrastructure stubs, and ChadBoar-specific extensions are documented."

2. **Domain mapping table:** All 8+2 bead types with ChadBoar-specific examples

3. **Bi-temporal query cookbook:** 5+ example queries that exercise WT/KT semantics:
   - "What signals did we generate during Asian session?"
   - "What did we know at midnight about today's whale activity?"
   - "Show all PROPOSAL_REJECTED in the last 24h with WARDEN_VETO category"
   - "Average refinery latency for SIGNAL beads this week"
   - "Walk lineage from this PROPOSAL_REJECTED back to its source FACTs"

4. **Shadow Field documentation:** How rejection beads accumulate, what makes them mineable, how Dream Cycle / SkillRL will consume them

5. **Invariant checklist:** Map each INV from the spec to its enforcement point in code

6. **Migration notes:** What changed from yesterday's v0 schema, why overnight beads are not migrated

---

### 7. Tests (`tests/test_bead_field.py`)

Comprehensive test suite:

**Schema validation (15+ tests):**
- Each bead type creates successfully with valid data
- OBSERVATION temporal_class requires world_time_valid_from AND world_time_valid_to
- PATTERN temporal_class requires both world_times to be null
- PROPOSAL_REJECTED requires rejection_category and rejection_reason
- RISK_BREACH rejection requires rejection_policy_ref
- SIGNAL requires at least one entry in lineage (unless explicitly root)
- hash_self is deterministic (same content → same hash)
- bead_id is UUID v7 format

**Chain integrity (5+ tests):**
- Write 10 beads, verify chain, tamper with one, verify detection
- Hash chain links correctly (prev_hash matches prior bead's hash_self)
- Concurrent writes don't corrupt (WAL mode)

**Bi-temporal queries (5+ tests):**
- query_world_time_range returns correct beads
- query_knowledge_at returns only beads with KT <= given time
- refinery_latency computes correctly
- DERIVED temporal bounding: world_time inherited from OBSERVATION inputs only

**Edge traversal (5+ tests):**
- get_lineage returns direct parents
- get_descendants returns beads referencing this one
- walk_lineage recurses to correct depth
- Shadow field query returns only PROPOSAL_REJECTED

**Merkle (3+ tests):**
- Batch creation with correct bead count
- Merkle root deterministic
- Trigger detection (decision boundary, max beads, max time)

**Signing (3+ tests):**
- ECDSA signature validates on read
- Tampered bead fails signature verification
- Attestation envelope populated correctly

---

### 8. Cleanup

- Remove old `lib/beads/schema.py` and `lib/beads/chain.py` (yesterday's v0)
- Keep `lib/chain/bead_chain.py` (legacy flight recorder) — it coexists
- Keep `lib/edge/bank.py` (legacy edge bank) — it coexists
- Update `tests/test_beads.py` → rename or replace with `tests/test_bead_field.py`
- The overnight beads in `state/beads.db` will be overwritten by new schema — that's fine

---

## Rules

- Pydantic v2 strict mode throughout
- All datetimes are UTC ISO 8601 with microsecond precision
- SQLite WAL mode for concurrent read/write safety
- bead_id = UUID v7 (use `uuid7` package or implement). NOT content hash.
- hash_self = SHA-256 of canonical JSON (sorted keys, no whitespace)
- Every bead write wrapped in try/except — failed bead write NEVER blocks heartbeat pipeline
- `--dangerously-skip-permissions` is active — move fast, test thoroughly
- When in doubt about a design choice, match the BEAD_FIELD_SPEC v0.2 language exactly

---

## Success Criteria

After implementation:
1. All tests pass
2. Gateway restarts and runs 3+ heartbeat cycles
3. `chain.get_chain_stats()` shows beads of types: FACT, SIGNAL, PROPOSAL_REJECTED, HEARTBEAT, POLICY, MODEL_VERSION
4. `chain.query_shadow_field()` returns PROPOSAL_REJECTED beads with full rejection context
5. `chain.refinery_latency()` returns meaningful WT-KT deltas
6. `chain.verify_chain()` reports intact
7. BEAD_FIELD_SPEC_IMPL.md is clean enough to hand to an a8ra agent

---

## Expected Bead Volume Per Cycle

Rough estimate for calibration:

| Type | Per Cycle | Per Day (144 cycles) |
|---|---|---|
| FACT | 3-5 (per data source) | ~500 |
| CLAIM | 0-2 (regime assessments, conditional) | ~100 |
| SIGNAL | 10-20 (all scored candidates) | ~2,000 |
| PROPOSAL | 1-5 (passed threshold) | ~300 |
| PROPOSAL_REJECTED | 10-15 (vetoed/discarded) | ~1,800 |
| SKILL | 0 (no Dream Cycle yet) | 0 |
| MODEL_VERSION | 1 (on restart only) | ~1 |
| POLICY | 1 (on restart only) | ~1 |
| HEARTBEAT | 1 | 144 |
| AUTOPSY | 0-3 (trade closes) | ~200 |
| **TOTAL** | **~30-45** | **~5,000** |

At ~5,000 beads/day with average ~2KB each, that's ~10MB/day. SQLite handles this trivially. The Shadow Field alone will have ~1,800 entries/day — rich fuel for future mining.

---

*Take the time to get this right. This is the a8ra substrate running in canary mode. Every edge case found here saves weeks in production.*
