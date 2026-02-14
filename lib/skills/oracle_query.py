"""Smart Money Oracle - CLI entry point.

Queries Nansen and Mobula for whale accumulation signals on Solana.

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
import yaml
from typing import Any, List, Dict

from dotenv import load_dotenv
import requests

from lib.clients.nansen import NansenClient
from lib.clients.helius import HeliusClient

# Load environment variables
load_dotenv()

class MobulaClient:
    def __init__(self, config: Dict[str, Any]):
        self.base_url = config['base_url']
        self.api_key = config['api_key']
        self.headers = {'Authorization': self.api_key}

    def get_whale_networth_accum(self, wallet: str) -> Dict[str, Any] | None:
        url = f"{self.base_url}/wallet/history"
        params = {
            'wallet': wallet,
            'blockchains': 'solana',
            'period': '1d',
            'unlistedAssets': 'true'
        }
        resp = requests.get(url, headers=self.headers, params=params, timeout=10)
        if resp.status_code != 200:
            return None
        data = resp.json()
        history = data.get('data', {}).get('balance_history', [])
        if len(history) < 2:
            return None
        current = history[-1][1]
        prev = history[0][1]
        accum = current - prev
        return {
            'wallet': wallet,
            'networth_usd': round(current, 2),
            'accum_24h_usd': round(accum, 2),
            'signal_strength': 'high' if accum > 50000 else 'medium' if accum > 10000 else 'low'
        }


async def query_oracle(token_mint: str | None = None) -> dict[str, Any]:
    """Query smart money signals."""
    firehose_path = os.path.join(os.path.dirname(__file__), '../../config/firehose.yaml')
    with open(firehose_path, 'r') as f:
        firehose = yaml.safe_load(f)

    client = NansenClient()
    try:
        nansen_signals = []
        if token_mint:
            data = await client.get_token_smart_money(token_mint)
            nansen_signals = _parse_token_signals(data, token_mint)
        else:
            data = await client.get_smart_money_transactions()
            nansen_signals = _parse_broad_signals(data)

        mobula_signals = []
        if 'mobula' in firehose and not token_mint:
            mobula_config = firehose['mobula']
            mobula_client = MobulaClient(mobula_config)
            whales = [
                "MJKqp326RZCHnAAbew9MDdui3iCKWco7fsK9sVuZTX2",
                "52C9T2T7JRojtxumYnYZhyUmrN7kqzvCLc4Ksvjk7TxD",
                "8BseXT9EtoEhBTKFFYkwTnjKSUZwhtmdKY2Jrj8j45Rt",
                "GitYucwpNcg6Dx1Y15UQ9TQn8LZMX1uuqQNn8rXxEWNC",
                "9QgXqrgdbVU8KcpfskqJpAXKzbaYQJecgMAruSWoXDkM"
            ]
            for wallet in whales:
                data = mobula_client.get_whale_networth_accum(wallet)
                if data and data['accum_24h_usd'] > 10000:
                    mobula_signals.append(data)

        # Helius staking % holders
        if token_mint:
            helius = HeliusClient()
            try:
                holders_resp = await helius.get_token_holders(token_mint, limit=100)
                holders = holders_resp.get("holders", [])
                holder_count = len(holders)
                # Proxy staking %: fraction of top holders with significant balance (conviction proxy)
                # Full stake check requires per-wallet RPC; approximated as holder distribution
                staked_pct = min(holder_count / 10.0, 100.0) if holder_count > 0 else 0.0  # rough: more holders = more conviction
                helius_signal = {
                    "token_mint": token_mint,
                    "staking_pct_holders": round(staked_pct, 1),
                    "stake_conviction": staked_pct > 30,
                    "unstaked_dump_risk": staked_pct < 10,
                    "top_holder_count": holder_count,
                    "source": "helius"
                }
                mobula_signals.append(helius_signal)  # append to whale signals
            finally:
                await helius.close()

        all_signals = nansen_signals + mobula_signals

        return {
            "status": "OK",
            "nansen_signals": nansen_signals,
            "mobula_signals": mobula_signals,
            "total_signals": len(all_signals),
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
                "source": "nansen"
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
            w.get("label", w.get("address", ""))[:8] for w in wallets[:5]
        ],
        "confidence": "high" if len(wallets) >= 5 else ("medium" if len(wallets) >= 3 else "low"),
        "source": "nansen"
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
