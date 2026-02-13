---
name: smart-money-oracle
description: Detect whale accumulation and smart money flows on Solana tokens.
metadata: {"openclaw": {"requires": {"bins": ["python3"], "env": ["NANSEN_API_KEY"]}}}
---

# Smart Money Oracle

## When to use
Run during heartbeat step 5 to detect whale accumulation signals.
Call on demand when G asks about smart money activity on a token.

## How to use
```bash
python3 -m lib.skills.oracle_query
```

Optional: query a specific token:
```bash
python3 -m lib.skills.oracle_query --token <MINT_ADDRESS>
```

## Output format
Returns JSON:
```json
{
  "signals": [
    {
      "token_mint": "...",
      "token_symbol": "...",
      "wallet_count": 5,
      "total_buy_sol": 12.5,
      "notable_wallets": ["label1", "label2"],
      "confidence": "high|medium|low"
    }
  ],
  "timestamp": "2026-02-10T12:00:00Z"
}
```

## Interpretation
- 3+ independent wallets buying = strong signal
- Known MEV/sandwich bots are pre-filtered out
- Cross-reference with Narrative Hunter for signal convergence
- Single signal alone is NOT enough to trade

## Limitations
- Nansen data has 5-15 min lag
- Only covers wallets Nansen tracks (not all wallets)
- API rate limited to 2 req/sec
