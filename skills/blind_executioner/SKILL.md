---
name: blind-executioner
description: Execute Jupiter swaps with MEV protection via Jito bundles. Blind KeyMan signer.
metadata: {"openclaw": {"requires": {"bins": ["python3"], "env": ["HELIUS_API_KEY"]}}}
---

# Blind Executioner — Trade Execution

## When to use
Run during heartbeat steps 8 (exits) and 12 (entries) to execute swaps.
Call on demand when G explicitly requests a trade.

## How to use
```bash
# Buy
python3 -m lib.skills.execute_swap --direction buy --token <MINT> --amount <SOL_AMOUNT>

# Sell
python3 -m lib.skills.execute_swap --direction sell --token <MINT> --amount <TOKEN_AMOUNT>

# Dry run (simulate only)
python3 -m lib.skills.execute_swap --direction buy --token <MINT> --amount <SOL> --dry-run
```

## Output format
Returns JSON:
```json
{
  "status": "SUCCESS|FAILED|DRY_RUN",
  "tx_signature": "...",
  "direction": "buy|sell",
  "token_mint": "...",
  "amount_in": "...",
  "amount_out": "...",
  "price_usd": 0.001234,
  "slippage_pct": 1.2,
  "jito_tip_lamports": 5000,
  "error": null
}
```

## Trade Gates (enforce BEFORE calling this skill)
- ≤$50 → auto-execute (no additional checks needed)
- $50-$100 → require 2+ signal convergence (verify before calling)
- >$100 → DO NOT CALL. Alert G on Telegram and wait. (INV-HUMAN-GATE-100)

## Security (INV-BLIND-KEY)
- The signer runs as a separate subprocess.
- This skill constructs the unsigned transaction and passes it to the signer.
- The private key NEVER enters your context, logs, or any output.
- You NEVER see, request, or reference the private key.

## Dynamic Fees
Queries recent slot base fees and sets priority tip relative to current
network congestion. Not a static value.
