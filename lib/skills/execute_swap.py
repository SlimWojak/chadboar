"""Blind Executioner — CLI entry point.

Executes Jupiter swaps with MEV protection via Jito bundles.
The signer is a separate subprocess (INV-BLIND-KEY).

Usage:
    python3 -m lib.skills.execute_swap --direction buy --token <MINT> --amount <SOL>
    python3 -m lib.skills.execute_swap --direction sell --token <MINT> --amount <AMOUNT>
    python3 -m lib.skills.execute_swap --direction buy --token <MINT> --amount <SOL> --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

import os

import httpx

from lib.clients.jupiter import JupiterClient, SOL_MINT
from lib.clients.jito import JitoClient
from lib.signer.keychain import sign_transaction, verify_isolation, SignerError


LAMPORTS_PER_SOL = 1_000_000_000


async def execute_swap(
    direction: str,
    token_mint: str,
    amount: float,
    dry_run: bool = False,
    slippage_bps: int = 300,
    wallet_pubkey: str = "",
) -> dict[str, Any]:
    """Execute a swap via Jupiter + Jito with Blind KeyMan signing.

    Flow:
    1. Get Jupiter quote
    2. Get swap transaction (unsigned)
    3. Pass to Blind KeyMan signer subprocess (INV-BLIND-KEY)
    4. Submit signed tx via Jito bundle (MEV-protected)
    """
    jupiter = JupiterClient()
    jito = JitoClient()

    try:
        # Verify key isolation before any execution
        isolation = verify_isolation()
        if isolation["status"] == "VIOLATION":
            return {
                "status": "FAILED",
                "direction": direction,
                "token_mint": token_mint,
                "error": f"KEY ISOLATION VIOLATION: {isolation['message']}",
            }

        # Get quote
        if direction == "buy":
            amount_lamports = int(amount * LAMPORTS_PER_SOL)
            quote = await jupiter.get_quote_buy(token_mint, amount_lamports, slippage_bps)
        else:
            quote = await jupiter.get_quote_sell(token_mint, int(amount), slippage_bps)

        if dry_run:
            return {
                "status": "DRY_RUN",
                "direction": direction,
                "token_mint": token_mint,
                "amount_in": str(quote.get("inAmount", amount)),
                "amount_out": str(quote.get("outAmount", "0")),
                "price_impact_pct": float(quote.get("priceImpactPct", 0)),
                "slippage_bps": slippage_bps,
                "route_plan": _summarize_route(quote),
                "message": "Dry run — no transaction executed.",
            }

        # ── LIVE EXECUTION ──────────────────────────────────────

        if not wallet_pubkey:
            return {
                "status": "FAILED",
                "direction": direction,
                "token_mint": token_mint,
                "error": "No wallet public key configured. Set in state/state.json.",
            }

        # Step 2: Get unsigned swap transaction from Jupiter
        swap_response = await jupiter.get_swap_transaction(
            quote_response=quote,
            user_public_key=wallet_pubkey,
        )
        unsigned_tx_b64 = swap_response.get("swapTransaction", "")
        if not unsigned_tx_b64:
            return {
                "status": "FAILED",
                "direction": direction,
                "token_mint": token_mint,
                "error": "Jupiter returned no swap transaction.",
            }

        # Step 3: Sign via Blind KeyMan (subprocess isolation)
        try:
            signed_tx_b64 = sign_transaction(unsigned_tx_b64)
        except SignerError as e:
            return {
                "status": "FAILED",
                "direction": direction,
                "token_mint": token_mint,
                "error": f"Signer error: {e}",
            }

        # Step 4: Submit via Jito bundle (MEV-protected), fallback to RPC
        tx_id = ""
        submit_via = ""
        try:
            bundle_result = await jito.send_bundle([signed_tx_b64])
            tx_id = bundle_result.get("result", "")
            submit_via = "jito"
        except Exception:
            # Jito failed (no tip, rate limit, etc.) — send via Helius RPC
            helius_key = os.environ.get("HELIUS_API_KEY", "")
            rpc_url = (
                f"https://mainnet.helius-rpc.com/?api-key={helius_key}"
                if helius_key
                else "https://api.mainnet-beta.solana.com"
            )
            async with httpx.AsyncClient(timeout=15) as rpc:
                rpc_resp = await rpc.post(rpc_url, json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "sendTransaction",
                    "params": [
                        signed_tx_b64,
                        {"encoding": "base64", "skipPreflight": False,
                         "preflightCommitment": "confirmed"},
                    ],
                })
                rpc_data = rpc_resp.json()
                if "error" in rpc_data:
                    return {
                        "status": "FAILED",
                        "direction": direction,
                        "token_mint": token_mint,
                        "error": f"RPC send failed: {rpc_data['error']}",
                    }
                tx_id = rpc_data.get("result", "")
                submit_via = "helius_rpc"

        return {
            "status": "SUCCESS",
            "direction": direction,
            "token_mint": token_mint,
            "amount_in": str(quote.get("inAmount", amount)),
            "amount_out": str(quote.get("outAmount", "0")),
            "price_impact_pct": float(quote.get("priceImpactPct", 0)),
            "slippage_bps": slippage_bps,
            "route_plan": _summarize_route(quote),
            "tx_signature": tx_id,
            "submit_via": submit_via,
            "message": f"Trade executed via {submit_via}. Tx: {tx_id}",
        }

    except Exception as e:
        return {
            "status": "FAILED",
            "direction": direction,
            "token_mint": token_mint,
            "error": str(e),
        }
    finally:
        await jupiter.close()
        await jito.close()


def _summarize_route(quote: dict[str, Any]) -> list[str]:
    """Summarize the swap route for logging."""
    route_plan = quote.get("routePlan", [])
    if not isinstance(route_plan, list):
        return []
    return [
        f"{step.get('swapInfo', {}).get('label', 'Unknown')} "
        f"({step.get('percent', 100)}%)"
        for step in route_plan
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Blind Executioner — Trade Execution")
    parser.add_argument("--direction", required=True, choices=["buy", "sell"])
    parser.add_argument("--token", required=True, help="Token mint address")
    parser.add_argument("--amount", required=True, type=float, help="Amount (SOL for buy, tokens for sell)")
    parser.add_argument("--dry-run", action="store_true", help="Simulate only")
    parser.add_argument("--slippage", type=int, default=300, help="Max slippage in bps (default: 300 = 3%%)")
    args = parser.parse_args()

    result = asyncio.run(execute_swap(
        direction=args.direction,
        token_mint=args.token,
        amount=args.amount,
        dry_run=args.dry_run,
        slippage_bps=args.slippage,
    ))
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] in ("DRY_RUN", "SUCCESS") else 1)


if __name__ == "__main__":
    main()
