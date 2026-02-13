"""Rug Warden — CLI entry point.

Pre-trade token validation. 6-point check:
1. Liquidity depth
2. Holder concentration
3. Mint/freeze authority
4. Honeypot simulation
5. Token age
6. LP lock status

Usage:
    python3 -m lib.skills.warden_check --token <MINT_ADDRESS>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from lib.clients.birdeye import BirdeyeClient
from lib.config import load_risk_config


async def check_token(mint: str) -> dict[str, Any]:
    """Run all 6 Rug Warden checks on a token."""
    risk = load_risk_config().get("rug_warden", {})
    birdeye = BirdeyeClient()

    checks: dict[str, Any] = {}
    reasons: list[str] = []
    verdict = "PASS"

    try:
        # Get token overview + security data
        overview = await birdeye.get_token_overview(mint)
        security = await birdeye.get_token_security(mint)

        overview_data = overview.get("data", overview)
        security_data = security.get("data", security)

        # 1. Liquidity check
        liquidity = float(overview_data.get("liquidity", 0))
        min_liq = risk.get("min_liquidity_usd", 10000)
        checks["liquidity_usd"] = liquidity
        if liquidity < min_liq:
            verdict = "FAIL"
            reasons.append(f"Liquidity ${liquidity:,.0f} < ${min_liq:,.0f} minimum")

        # 2. Holder concentration
        top_holder_pct = float(security_data.get("top10HolderPercent", 0)) * 100
        max_conc = risk.get("max_holder_concentration_pct", 80)
        checks["holder_concentration_pct"] = round(top_holder_pct, 1)
        if top_holder_pct > max_conc:
            verdict = "FAIL"
            reasons.append(f"Top 10 holders control {top_holder_pct:.1f}% (> {max_conc}%)")

        # 3. Mint/freeze authority
        mint_mutable = bool(security_data.get("isMintable", False))
        freeze_mutable = bool(security_data.get("isFreezable", False))
        checks["mint_authority_mutable"] = mint_mutable
        checks["freeze_authority_mutable"] = freeze_mutable
        if risk.get("reject_mutable_mint", True) and (mint_mutable or freeze_mutable):
            verdict = "FAIL"
            reasons.append(f"Mutable authority: mint={mint_mutable}, freeze={freeze_mutable}")

        # 4. Honeypot simulation (simplified — check if sellable)
        # Full honeypot sim requires Helius transaction simulation
        checks["honeypot_simulation"] = "SKIPPED"  # Implemented in Phase 3 with signer

        # 5. Token age
        creation_time = overview_data.get("createdAt", 0)
        if creation_time:
            import time
            age_seconds = int(time.time() - creation_time / 1000)
            checks["token_age_seconds"] = age_seconds
            min_age = risk.get("min_token_age_seconds", 300)
            if age_seconds < min_age:
                if verdict != "FAIL":
                    verdict = "WARN"
                reasons.append(f"Token age {age_seconds}s < {min_age}s (very new)")
        else:
            checks["token_age_seconds"] = -1

        # 6. LP lock status
        lp_locked = bool(security_data.get("isLpLocked", False))
        lp_burned = bool(security_data.get("isLpBurned", False))
        checks["lp_locked"] = lp_locked or lp_burned
        if not (lp_locked or lp_burned) and not risk.get("reject_unlocked_lp", False):
            if verdict != "FAIL":
                verdict = "WARN"
            reasons.append("LP not locked or burned")

    except Exception as e:
        verdict = "FAIL"
        reasons.append(f"Check failed: {e}")
    finally:
        await birdeye.close()

    return {
        "verdict": verdict,
        "token_mint": mint,
        "checks": checks,
        "reasons": reasons,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Rug Warden — Pre-trade validation")
    parser.add_argument("--token", required=True, help="Token mint address")
    args = parser.parse_args()

    result = asyncio.run(check_token(args.token))
    print(json.dumps(result, indent=2))

    exit_code = 0 if result["verdict"] == "PASS" else (2 if result["verdict"] == "WARN" else 1)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
