---
name: flight-recorder
description: Tamper-evident hash chain with Solana on-chain anchoring for action integrity.
metadata: {"openclaw": {"requires": {"bins": ["python3"], "env": ["HELIUS_API_KEY"]}}}
---

# Flight Recorder (Chain Status)

## When to use
- Boot sequence step 1c: automatic chain verification
- On demand when G asks about chain health or integrity
- Debugging after suspected tampering or data corruption

## How to use
```bash
# Summary (quick health check)
python3 -m lib.skills.chain_status

# Full chain verification (genesis to tip)
python3 -m lib.skills.chain_status --verify

# Recent beads (last N entries)
python3 -m lib.skills.chain_status --recent 10
```

## Output format
```json
{
  "status": "OK",
  "chain_length": 1234,
  "last_anchor": {"tx": "abc...", "seq": 1200, "timestamp": "..."},
  "beads_since_anchor": 34,
  "chain_integrity": "CLEAN"
}
```

## Chain architecture
- Every significant action (trade, heartbeat, guard alert) is recorded as a hash-chained bead
- Each bead's SHA-256 hash includes the previous bead's hash (tamper-evident chain)
- Every 50 beads, a Merkle root is anchored to Solana via SPL Memo (~$0.0004/anchor)
- On boot, the chain is verified from the last anchor forward

## Bead types
| Type | Trigger |
|------|---------|
| `heartbeat` | End of each heartbeat cycle |
| `trade_entry` | EdgeBank.write_bead() for entry |
| `trade_exit` | EdgeBank.write_bead() for exit |
| `anchor` | Auto-triggered every 50 beads |
| `guard_alert` | Guard halt events |
| `state_change` | Significant state mutations |

## Interpretation
- `CLEAN`: Chain integrity verified, no tampering detected
- `UNANCHORED`: Chain valid but no on-chain anchors yet
- `TAMPERED`: Hash mismatch detected — integrity violation
