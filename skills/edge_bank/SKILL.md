---
name: edge-bank
description: Trade autopsy bead storage and vector recall for pattern learning.
---

# Edge Bank — Bead Memory

## When to use
- **After every trade** (entry or exit): write an autopsy bead (heartbeat steps 8, 12)
- **Before any new entry** (heartbeat step 10): query similar historical patterns
- **On demand**: when G asks about past trades or patterns

## Writing a bead
```bash
python3 -m lib.skills.bead_write --type <entry|exit> --data '<JSON>'
```

Data JSON should include:
```json
{
  "token_mint": "...",
  "token_symbol": "...",
  "direction": "buy|sell",
  "amount_sol": 0.5,
  "price_usd": 0.001234,
  "thesis": "Whale accumulation + X narrative convergence",
  "signals": ["oracle:3_wallets", "narrative:5x_volume", "warden:PASS"],
  "outcome": "pending|win|loss",
  "pnl_pct": 0.0,
  "exit_reason": "",
  "market_conditions": "bullish momentum, SOL at $180"
}
```

## Querying similar patterns
```bash
python3 -m lib.skills.bead_query --context '<SIGNAL_SUMMARY>'
```

Returns top 3 most similar historical trades:
```json
{
  "matches": [
    {
      "similarity": 0.87,
      "token_symbol": "PREV_TOKEN",
      "outcome": "loss",
      "pnl_pct": -18.5,
      "thesis": "Similar whale pattern, rugged within 1h",
      "date": "2026-02-08"
    }
  ],
  "total_beads": 15
}
```

## Storage
- `beads/` directory: one markdown file per trade (timestamped)
- `edge.db`: SQLite with text + vector columns for similarity search
- Vector model: all-MiniLM-L6-v2 (80MB, runs locally)

## Purpose
Compound learning across cycles. Every trade teaches something.
Losses are MORE valuable than wins — document them thoroughly.
