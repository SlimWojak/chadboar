---
name: rug-warden
description: Pre-trade token validation. MUST run before ANY trade. FAIL = no trade.
metadata: {"openclaw": {"requires": {"bins": ["python3"], "env": ["HELIUS_API_KEY", "BIRDEYE_API_KEY"]}}}
---

# Rug Warden — Pre-Trade Validation

## When to use
Run this skill BEFORE every trade attempt. This is non-optional.
If this skill returns FAIL, the trade MUST NOT execute. (INV-RUG-WARDEN-VETO)

## How to use
```bash
python3 -m lib.skills.warden_check --token <MINT_ADDRESS>
```

## Output format
Returns JSON:
```json
{
  "verdict": "PASS|FAIL|WARN",
  "token_mint": "...",
  "checks": {
    "liquidity_usd": 45000,
    "holder_concentration_pct": 35,
    "mint_authority_mutable": false,
    "freeze_authority_mutable": false,
    "honeypot_simulation": "PASS",
    "token_age_seconds": 3600,
    "lp_locked": true
  },
  "reasons": ["..."]
}
```

## Rules (NON-NEGOTIABLE)
- `FAIL` → trade does not execute. No override. No exceptions.
- `WARN` → proceed only with 3+ signal convergence.
- `PASS` → proceed normally with standard conviction assessment.

## Checks performed
1. Liquidity depth — reject if < $10k
2. Holder concentration — reject if top 10 wallets > 80%
3. Mint/freeze authority — reject if mutable
4. Honeypot simulation — simulate sell tx, reject if fails
5. Token age — warn if < 5 min old
6. LP lock status — warn if LP not locked/burned
