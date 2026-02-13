"""Smart Money Oracle — CLI entry point.

Queries Nansen for whale accumulation signals on Solana.

Usage:
    python3 -m lib.skills.oracle_query
    python3 -m lib.skills.oracle_query --token <MINT_ADDRESS>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

from dotenv import load_dotenv

from lib.clients.nansen import NansenClient

# Load environment variables
load_dotenv()


async def query_oracle(token_mint: str | None = None) -> dict[str, Any]:
    """Query smart money signals."""
    client = NansenClient()
    try:
        if token_mint:
            data = await client.get_token_smart_money(token_mint)
            signals = _parse_token_signals(data, token_mint)
        else:
            data = await client.get_smart_money_transactions()
            signals = _parse_broad_signals(data)

        return {
            "status": "OK",
            "signals": signals,
            "count": len(signals),
        }
    except Exception as e:
        return {
            "status": "ERROR",
            "error": str(e),
            "signals": [],
            "count": 0,
        }
    finally:
        await client.close()


def _parse_broad_signals(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse broad smart money transaction data into signals."""
    # Group by token, count unique wallets
    SOL_MINT = "So11111111111111111111111111111111111111112"
    token_wallets: dict[str, dict[str, Any]] = {}
    transactions = data.get("data", data.get("transactions", []))
    if not isinstance(transactions, list):
        return []

    for tx in transactions:
        # Infer BUY: sold SOL, bought token
        token_sold = tx.get("token_sold_address", "")
        token_bought = tx.get("token_bought_address", "")
        
        if token_sold == SOL_MINT and token_bought != SOL_MINT:
            # This is a BUY (spent SOL to get token)
            mint = token_bought
            symbol = tx.get("token_bought_symbol", "UNKNOWN")
            value_usd = tx.get("trade_value_usd", 0)
        elif token_bought == SOL_MINT and token_sold != SOL_MINT:
            # This is a SELL (sold token for SOL) — skip
            continue
        else:
            # Token-to-token swap or unknown — skip
            continue
        
        wallet = tx.get("trader_address", "")
        if not mint or not wallet:
            continue

        if mint not in token_wallets:
            token_wallets[mint] = {
                "token_mint": mint,
                "token_symbol": symbol,
                "wallets": set(),
                "total_value_usd": 0.0,
            }

        token_wallets[mint]["wallets"].add(wallet)
        token_wallets[mint]["total_value_usd"] += float(value_usd)

    # Filter: require 3+ independent wallets
    signals = []
    for info in token_wallets.values():
        wallet_count = len(info["wallets"])
        if wallet_count >= 3:
            confidence = "high" if wallet_count >= 5 else "medium"
            signals.append({
                "token_mint": info["token_mint"],
                "token_symbol": info["token_symbol"],
                "wallet_count": wallet_count,
                "total_buy_usd": round(info["total_value_usd"], 2),
                "confidence": confidence,
            })

    signals.sort(key=lambda s: s["wallet_count"], reverse=True)
    return signals[:10]


def _parse_token_signals(data: dict[str, Any], mint: str) -> list[dict[str, Any]]:
    """Parse token-specific smart money data."""
    wallets = data.get("data", data.get("wallets", []))
    if not isinstance(wallets, list):
        return []

    return [{
        "token_mint": mint,
        "wallet_count": len(wallets),
        "notable_wallets": [
            w.get("label", w.get("address", "")[:8]) for w in wallets[:5]
        ],
        "confidence": "high" if len(wallets) >= 5 else ("medium" if len(wallets) >= 3 else "low"),
    }]


def main() -> None:
    parser = argparse.ArgumentParser(description="Smart Money Oracle")
    parser.add_argument("--token", help="Specific token mint to query")
    args = parser.parse_args()

    result = asyncio.run(query_oracle(args.token))
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] == "OK" else 1)


if __name__ == "__main__":
    main()
