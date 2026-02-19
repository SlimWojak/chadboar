# Bead Architecture — Intelligence Substrate

ChadBoar's structured memory. Every signal, verdict, trade, and reflection
is captured as a hash-linked bead with mandatory provenance edges.

## Three Layers

Every bead has three layers:

1. **Header** — identity, type, chain link, timing, agent provenance
2. **Edges** — derived_from, supports, contradicts (mandatory graph structure)
3. **Payload** — type-specific data

Bead IDs are hex-encoded SHA-256 of canonical JSON content (deterministic).
Isolated knowledge doesn't compound — only connected knowledge does.

## Bead Types

| Type | Purpose | Emitted By |
|------|---------|------------|
| `signal` | Raw candidate entering funnel | oracle_query, narrative scan |
| `verdict` | Scored candidate with conviction breakdown | scoring pipeline |
| `trade` | Executed trade (live or paper) | trade execution, paper_trade |
| `autopsy` | Post-trade PnL evaluation + reflection | paper_trade PnL check |
| `insight` | Distilled pattern from mining | post-hoc analysis |
| `heartbeat` | Cycle-level metadata + health | heartbeat_runner |

## Edge Discipline

Edges are mandatory. Every bead must declare its lineage:

- **derived_from** — what inputs informed this bead (min 1, except heartbeat)
- **supports** — beads whose findings this bead agrees with
- **contradicts** — beads whose findings this bead disagrees with
- **edges_complete** — honest self-declaration of edge population quality

Special rules:
- Heartbeat beads derive from the previous heartbeat (chain manager sets this)
- Autopsy beads must reference their trade bead in derived_from
- Autopsy beads should support or contradict the original verdict

If edges can't be fully populated, `edges_complete=False` with a reason.

## Chain Manager

`lib/beads/chain.py` — append-only SQLite storage with hash-linked integrity.

**DB file:** `state/beads.db` (separate from legacy `edge.db`)

### API

```python
from lib.beads import BeadChain, Bead, BeadHeader, BeadType, SignalPayload

chain = BeadChain()

# Write
bead = Bead(
    header=BeadHeader(bead_type=BeadType.SIGNAL),
    payload=SignalPayload(
        token_mint="...", token_symbol="TEST",
        play_type="graduation", discovery_source="pulse-bonding",
    ),
)
bead_id = chain.write_bead(bead)  # returns SHA-256 hex

# Read
bead = chain.get_bead(bead_id)
head = chain.get_chain_head()

# Query
signals = chain.query_by_type(BeadType.SIGNAL, limit=50)
token_beads = chain.query_by_token("So111...", limit=50)
linked = chain.query_by_edge(bead_id)  # beads referencing this one

# Verify
result = chain.verify_chain()  # ChainVerifyResult(valid, total, verified, message)

# Stats
stats = chain.get_chain_stats()  # {chain_length, type_counts, unique_tokens, ...}

# Export
count = chain.export_chain_jsonl("state/beads_export.jsonl")
```

### Write Flow

1. Pydantic validates schema
2. Timestamp set if empty
3. Edge discipline enforced (model validators)
4. `prev_hash` set from chain head (genesis uses `0*64`)
5. `bead_id` computed as SHA-256 of canonical content
6. Atomically inserted into SQLite

### Storage Schema

```sql
CREATE TABLE beads (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    bead_id     TEXT NOT NULL UNIQUE,
    prev_hash   TEXT NOT NULL,
    bead_type   TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    agent_id    TEXT NOT NULL,
    session_id  TEXT NOT NULL DEFAULT '',
    token_mint  TEXT NOT NULL DEFAULT '',
    payload     TEXT NOT NULL,    -- JSON
    edges       TEXT NOT NULL,    -- JSON
    provenance  TEXT NOT NULL,    -- JSON
    full_bead   TEXT NOT NULL     -- JSON (complete bead for reconstruction)
);
```

Indexed on `bead_type`, `token_mint`, `timestamp`.

## Integration Points

Where beads get emitted in the heartbeat pipeline:

### Pipeline Flow → Bead Lifecycle

```
Oracle Query → signal beads (per discovery source)
    ↓
Narrative Scan → signal beads (DexScreener/Birdeye)
    ↓
Scoring → verdict beads (derived_from: signal beads)
    ↓
Trade Gate → trade beads (derived_from: verdict bead)
    ↓
PnL Check → autopsy beads (derived_from: trade bead)
    ↓
Cycle End → heartbeat bead (derived_from: previous heartbeat)
```

### Emission Points

| File | Location | Bead Type | Derived From |
|------|----------|-----------|--------------|
| `heartbeat_runner.py` ~L849 | Cycle completion | `heartbeat` | previous heartbeat |
| `heartbeat_runner.py` ~L582 | Scoring complete | `verdict` | oracle + narrative signals |
| `heartbeat_runner.py` ~L751,790 | Paper trade / watchlist | `trade` | verdict bead |
| `oracle_query.py` ~L243 | Nansen dex-trades | `signal` | none (genesis for candidate) |
| `oracle_query.py` ~L430 | Mobula whale scan | `signal` | none |
| `oracle_query.py` ~L503 | Pulse bonding/bonded | `signal` | none |
| `paper_trade.py` ~L77 | PnL check | `autopsy` | trade bead |

### Provenance Tracking

Every bead records which data sources were consulted:

```python
provenance=BeadProvenance(
    data_sources={"dexscreener": "OK", "birdeye": "SKIP", "nansen": "OK"},
    source_hash="sha256-of-raw-input",
    attestation_coverage=0.7,  # 70% of inputs independently verifiable
)
```

## Relationship to Legacy Systems

| Legacy | New | Status |
|--------|-----|--------|
| `lib/chain/bead_chain.py` | `lib/beads/chain.py` | New system — structured beads |
| `lib/edge/bank.py` | `lib/beads/chain.py` | New system — unified storage |
| `edge.db` chain_beads table | `state/beads.db` beads table | Separate DB, no collision |
| `edge.db` beads table | `state/beads.db` beads table | Separate DB |

Legacy chain is the flight recorder (unstructured payloads, tamper-evident).
New bead chain is the intelligence substrate (structured payloads, mandatory edges).
Both can coexist during migration.

## a8ra Multi-Agent Port

Designed for portability:
- `agent_id` parameterized per-agent (default: `chadboar-v0.2`)
- Cross-agent edges via derived_from/supports/contradicts
- Gate-signing replaces self-signing
- `session_id` ties to OpenClaw cron run ID
