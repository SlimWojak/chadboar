---
name: narrative-hunter
description: Detect pre-pump narrative convergence from social + onchain signals.
metadata: {"openclaw": {"requires": {"bins": ["python3"], "env": ["X_BEARER_TOKEN", "BIRDEYE_API_KEY"]}}}
---

# Narrative Hunter

## When to use
Run during heartbeat step 6 to scan for narrative momentum.
Call on demand when G asks about sentiment on a token or sector.

## How to use
```bash
python3 -m lib.skills.narrative_scan
```

Optional: scan a specific token or topic:
```bash
python3 -m lib.skills.narrative_scan --token <MINT_ADDRESS>
python3 -m lib.skills.narrative_scan --topic "AI tokens"
```

## Output format
Returns JSON with decomposed factors (NOT a scalar score):
```json
{
  "signals": [
    {
      "token_mint": "...",
      "token_symbol": "...",
      "x_mentions_1h": 47,
      "x_mentions_vs_avg": "3x",
      "kol_tier": "2 mid-tier KOLs",
      "volume_1h_usd": 180000,
      "volume_vs_7d_avg": "5x",
      "holder_delta_1h": 340,
      "new_pool_detected": false,
      "pool_age_minutes": 45
    }
  ],
  "new_pools": [],
  "timestamp": "2026-02-10T12:00:00Z"
}
```

## IMPORTANT
No scalar "buy score." Output is factual decomposition only.
The agent interprets. The skill reports.
Example: "X mentions: 47 (3x avg), KOL: 2 mid-tier, Volume: $180k (5x avg)"

## New Pool Detection
Polls Helius for recently created Raydium/Pump.fun pools since last cycle.
Tracks last-seen timestamp in state/state.json.
