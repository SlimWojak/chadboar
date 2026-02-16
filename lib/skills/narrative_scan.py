"""Narrative Hunter — CLI entry point.

Scans social (X) + onchain (Birdeye) signals for narrative convergence.
Outputs decomposed factors — NO scalar score.

Usage:
    python3 -m lib.skills.narrative_scan
    python3 -m lib.skills.narrative_scan --token <MINT_ADDRESS>
    python3 -m lib.skills.narrative_scan --topic "AI tokens"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from lib.clients.birdeye import BirdeyeClient
from lib.clients.x_api import XClient


async def scan_narrative(
    token_mint: str | None = None,
    topic: str | None = None,
) -> dict[str, Any]:
    """Scan for narrative signals across social + onchain."""
    birdeye = BirdeyeClient()
    x_client = XClient()

    signals: list[dict[str, Any]] = []

    try:
        if token_mint:
            signal = await _scan_token(token_mint, birdeye, x_client)
            if signal:
                signals.append(signal)
        elif topic:
            # Topic-based scan (X search only)
            x_data = await x_client.search_recent(topic, max_results=50)
            tweets = x_data.get("data", [])
            signals.append({
                "topic": topic,
                "x_mentions_count": len(tweets) if isinstance(tweets, list) else 0,
                "source": "topic_scan",
            })
        else:
            # Broad scan: trending tokens
            trending = await birdeye.get_token_list_trending(limit=10)
            raw_data = trending.get("data", {})
            tokens = raw_data.get("tokens", raw_data) if isinstance(raw_data, dict) else raw_data
            if isinstance(tokens, list):
                for t in tokens[:5]:
                    mint = t.get("address", "")
                    if mint:
                        signal = await _scan_token(mint, birdeye, x_client)
                        if signal:
                            signals.append(signal)

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
        await birdeye.close()
        await x_client.close()


async def _scan_token(
    mint: str, birdeye: BirdeyeClient, x_client: XClient
) -> dict[str, Any] | None:
    """Scan a single token for narrative signals."""
    try:
        overview = await birdeye.get_token_overview(mint)
        data = overview.get("data", overview)
        symbol = data.get("symbol", "UNKNOWN")

        # Volume data
        volume_1h = float(data.get("v1hUSD", 0))
        volume_24h = float(data.get("v24hUSD", 0))
        avg_hourly = volume_24h / 24 if volume_24h > 0 else 0
        volume_ratio = round(volume_1h / avg_hourly, 1) if avg_hourly > 0 else 0

        # Holder data
        holder_count = int(data.get("holder", 0))

        # Birdeye viewers delta/spike (KOL proxy)
        viewers_10m = float(data.get("uniqueViewerCount10m", data.get("lc10m", 0)))
        avg_viewers_10m = float(data.get("avgViewerCount10m", viewers_10m / 2.0 if viewers_10m > 0 else 0))
        viewers_ratio = viewers_10m / avg_viewers_10m if avg_viewers_10m > 0 else 0

        # Momentum deltas
        h1_change_pct = float(data.get("h1Change", data.get("priceChange1hPercent", data.get("v1hChange", 0))))
        trades_resp = await birdeye.get_trades(mint, limit=50)
        raw_trades = trades_resp.get("data", {})
        trade_data = raw_trades.get("items", raw_trades) if isinstance(raw_trades, dict) else raw_trades
        if not isinstance(trade_data, list):
            trade_data = []
        large_buy_usd = 0.0
        large_sell_usd = 0.0
        if trade_data:
            avg_trade_usd = volume_1h / len(trade_data)
            for trade in trade_data:
                usd = float(trade.get("usdAmount", trade.get("quoteAmountUSD", trade.get("tradeAmountUSD", 0))))
                action = trade.get("action", trade.get("side", "")).lower()
                if usd > avg_trade_usd * 5 and usd > 5000:
                    if "buy" in action:
                        large_buy_usd += usd
                    elif "sell" in action:
                        large_sell_usd += usd
        whale_net_usd = large_buy_usd - large_sell_usd

        # X mentions (gracefully degrade if X API unavailable)
        mention_count = 0
        kol_count = 0
        try:
            x_data = await x_client.search_recent(f"${symbol} OR {symbol} solana", max_results=50)
            tweets = x_data.get("data", [])
            mention_count = len(tweets) if isinstance(tweets, list) else 0

            # KOL detection (verified accounts with 10k+ followers)
            users = {}
            for u in x_data.get("includes", {}).get("users", []):
                users[u.get("id")] = u
            if isinstance(tweets, list):
                for tweet in tweets:
                    author = users.get(tweet.get("author_id", ""), {})
                    followers = author.get("public_metrics", {}).get("followers_count", 0)
                    if followers >= 10000:
                        kol_count += 1
        except Exception:
            pass  # X API disabled — continue with onchain-only signals

        return {
            "token_mint": mint,
            "token_symbol": symbol,
            "x_mentions_1h": mention_count,
            "kol_mentions": kol_count,
            "volume_1h_usd": round(volume_1h, 2),
            "volume_vs_avg": f"{volume_ratio}x",
            "holder_count": holder_count,
            "viewers_10m": viewers_10m,
            "viewers_vs_avg": f"{viewers_ratio:.1f}x",
            "momentum_h1_pct": round(h1_change_pct, 2),
            "whale_net_usd": round(whale_net_usd, 0),
            "vol_ratio": volume_ratio,
        }
    except Exception:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Narrative Hunter")
    parser.add_argument("--token", help="Specific token mint to scan")
    parser.add_argument("--topic", help="Topic to search on X")
    args = parser.parse_args()

    result = asyncio.run(scan_narrative(args.token, args.topic))
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] == "OK" else 1)


if __name__ == "__main__":
    main()
