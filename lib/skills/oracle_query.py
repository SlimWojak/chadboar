"""Smart Money Oracle - CLI entry point.

Queries Nansen Token God Mode (TGM) suite for whale accumulation signals on Solana.
4-phase pipeline: Discovery → Validation → DCA Detection → Holdings Scan.
Falls back to legacy dex-trades scan if Token Screener is unavailable.

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
import time
import yaml
from typing import Any, List, Dict

from dotenv import load_dotenv
import requests

from lib.clients.nansen import NansenClient
from lib.clients.helius import HeliusClient

# Load environment variables (override=True: always use .env over stale inherited vars)
load_dotenv(override=True)

# Module-level diagnostics collector (reset per query_oracle call)
_diagnostics: list[str] = []


def _log(msg: str) -> None:
    """Print timestamped diagnostic to stderr (visible in heartbeat logs)."""
    ts = time.strftime("%H:%M:%S")
    line = f"[oracle {ts}] {msg}"
    print(line, file=sys.stderr)
    _diagnostics.append(line)

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

    def get_whale_portfolio(self, wallet: str) -> list[dict[str, Any]]:
        """Get wallet's Solana token holdings from Mobula portfolio API."""
        url = f"{self.base_url}/wallet/portfolio"
        params = {'wallet': wallet, 'blockchains': 'solana'}
        resp = requests.get(url, headers=self.headers, params=params, timeout=10)
        if resp.status_code != 200:
            return []
        data = resp.json()
        assets = data.get('data', {}).get('assets', [])
        tokens = []
        for asset in assets[:5]:  # Top 5 by value
            mint = asset.get('asset', {}).get('address', '')
            symbol = asset.get('asset', {}).get('symbol', 'UNKNOWN')
            value = float(asset.get('estimated_balance', 0))
            if mint and value > 1000:  # Only meaningful holdings
                tokens.append({
                    'token_mint': mint,
                    'token_symbol': symbol,
                    'value_usd': round(value, 2),
                })
        return tokens


async def query_oracle(token_mint: str | None = None) -> dict[str, Any]:
    """Query smart money signals using TGM pipeline with dex-trades fallback."""
    global _diagnostics
    _diagnostics = []
    phase_timing: dict[str, float] = {}
    t_total = time.monotonic()
    _log("Oracle query starting...")

    firehose_path = os.path.join(os.path.dirname(__file__), '../../config/firehose.yaml')
    with open(firehose_path, 'r') as f:
        firehose = yaml.safe_load(f)

    client = NansenClient()
    try:
        nansen_signals = []
        holdings_delta: list[dict[str, Any]] = []
        mobula_signals: list[dict[str, Any]] = []

        if token_mint:
            # Single-token mode: use existing netflow + enrich with TGM
            t0 = time.monotonic()
            _log("Single-token mode...")
            data = await client.get_token_smart_money(token_mint)
            nansen_signals = _parse_token_signals(data, token_mint)
            nansen_signals = await _enrich_signals(client, nansen_signals)
            phase_timing["single_token"] = round(time.monotonic() - t0, 1)

            # Helius staking % holders
            helius = HeliusClient()
            try:
                holders_resp = await helius.get_token_holders(token_mint, limit=100)
                holders = holders_resp.get("holders", [])
                holder_count = len(holders)
                staked_pct = min(holder_count / 10.0, 100.0) if holder_count > 0 else 0.0
                helius_signal = {
                    "token_mint": token_mint,
                    "staking_pct_holders": round(staked_pct, 1),
                    "stake_conviction": staked_pct > 30,
                    "unstaked_dump_risk": staked_pct < 10,
                    "top_holder_count": holder_count,
                    "source": "helius"
                }
                mobula_signals.append(helius_signal)
            finally:
                await helius.close()
        else:
            # Broad scan: run TGM and Mobula in parallel
            tasks_to_run: list = [_run_tgm_pipeline(client)]

            mobula_client = None
            whales: list[str] = []
            if 'mobula' in firehose:
                mobula_config = firehose['mobula']
                mobula_client = MobulaClient(mobula_config)
                whales = [
                    "MJKqp326RZCHnAAbew9MDdui3iCKWco7fsK9sVuZTX2",
                    "52C9T2T7JRojtxumYnYZhyUmrN7kqzvCLc4Ksvjk7TxD",
                    "8BseXT9EtoEhBTKFFYkwTnjKSUZwhtmdKY2Jrj8j45Rt",
                    "GitYucwpNcg6Dx1Y15UQ9TQn8LZMX1uuqQNn8rXxEWNC",
                    "9QgXqrgdbVU8KcpfskqJpAXKzbaYQJecgMAruSWoXDkM"
                ]
                tasks_to_run.append(_run_mobula_scan(mobula_client, whales))

            results = await asyncio.gather(*tasks_to_run, return_exceptions=True)

            # Unpack TGM result
            tgm_result = results[0]
            if isinstance(tgm_result, tuple):
                nansen_signals, holdings_delta, tgm_timing = tgm_result
                phase_timing.update(tgm_timing)
            elif isinstance(tgm_result, Exception):
                _log(f"TGM pipeline FAILED: {tgm_result}")

            # Unpack Mobula result
            if len(results) > 1:
                mobula_result = results[1]
                if isinstance(mobula_result, tuple):
                    mobula_signals, mobula_timing = mobula_result
                    phase_timing.update(mobula_timing)
                elif isinstance(mobula_result, Exception):
                    _log(f"Mobula scan FAILED: {mobula_result}")

        all_signals = nansen_signals + mobula_signals
        phase_timing["total"] = round(time.monotonic() - t_total, 1)
        _log(f"Oracle done: {len(all_signals)} signals in {phase_timing['total']}s")

        return {
            "status": "OK",
            "nansen_signals": nansen_signals,
            "holdings_delta": holdings_delta,
            "mobula_signals": mobula_signals,
            "total_signals": len(all_signals),
            "phase_timing": phase_timing,
            "diagnostics": list(_diagnostics),
        }
    except Exception as e:
        _log(f"Oracle FAILED: {e}")
        return {
            "status": "ERROR",
            "error": str(e),
            "nansen_signals": [],
            "holdings_delta": [],
            "mobula_signals": [],
            "total_signals": 0,
            "phase_timing": phase_timing,
            "diagnostics": list(_diagnostics),
        }
    finally:
        await client.close()


async def _run_tgm_pipeline(client: NansenClient) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, float]]:
    """Run full 4-phase TGM pipeline. Falls back to dex-trades if screener fails.

    Returns:
        (nansen_signals, holdings_delta, phase_timing)
    """
    phase_timing: dict[str, float] = {}

    # --- Phase 4: Holdings Scan (start early — doesn't depend on candidates) ---
    t4 = time.monotonic()
    _log("Phase 4: Holdings scan (parallel start)...")
    holdings_task = asyncio.create_task(_fetch_holdings(client))

    # --- Phase 1: Discovery (Token Screener, 5 credits) ---
    t1 = time.monotonic()
    candidates: list[dict[str, Any]] = []
    discovery_source = "screener"

    _log("Phase 1: Token Screener (1h)...")
    try:
        screener_data = await client.screen_tokens(
            chains=["solana"],
            timeframe="1h",
        )
        candidates = _parse_screener_candidates(screener_data)
        _log(f"Phase 1 done: {len(candidates)} candidates ({time.monotonic()-t1:.1f}s)")
    except Exception as e:
        _log(f"Phase 1 FAILED: {e} ({time.monotonic()-t1:.1f}s)")

    if not candidates:
        # 24h fallback before dex-trades
        _log("Screener 1h empty, trying 24h...")
        try:
            screener_data = await client.screen_tokens(
                chains=["solana"],
                timeframe="24h",
            )
            candidates = _parse_screener_candidates(screener_data)
            discovery_source = "screener-24h"
            _log(f"Screener 24h: {len(candidates)} candidates")
        except Exception as e:
            _log(f"Screener 24h failed: {e}")

    if not candidates:
        # Final fallback: dex-trades
        _log("Screener empty, falling back to dex-trades...")
        try:
            dex_data = await client.get_smart_money_transactions(limit=50)
            candidates = _parse_dex_trades_candidates(dex_data)
            discovery_source = "dex-trades"
            _log(f"Dex-trades: {len(candidates)} candidates")
        except Exception as e:
            _log(f"Dex-trades FAILED: {e}")
            holdings_delta = await holdings_task
            phase_timing["phase1_discovery"] = round(time.monotonic() - t1, 1)
            return [], holdings_delta, phase_timing

    phase_timing["phase1_discovery"] = round(time.monotonic() - t1, 1)

    # Limit to top 5 candidates for enrichment
    candidates = candidates[:5]

    # --- Phase 2: Validation (Flow Intel + Who Bought/Sold, parallel, 1 credit each) ---
    t2 = time.monotonic()
    _log(f"Phase 2: Enriching {len(candidates)} candidates...")
    candidates = await _enrich_signals(client, candidates)
    phase_timing["phase2_enrichment"] = round(time.monotonic() - t2, 1)
    _log(f"Phase 2 done ({time.monotonic()-t2:.1f}s)")

    # --- Phase 3: DCA Detection (top 3 candidates) ---
    t3 = time.monotonic()
    _log("Phase 3: DCA detection...")
    dca_tasks = []
    for sig in candidates[:3]:
        mint = sig.get("token_mint", "")
        if mint:
            dca_tasks.append(_fetch_dca_count(client, mint))
    if dca_tasks:
        dca_results = await asyncio.gather(*dca_tasks, return_exceptions=True)
        for i, dca_result in enumerate(dca_results):
            if i < len(candidates) and isinstance(dca_result, int):
                candidates[i]["dca_count"] = dca_result
    phase_timing["phase3_dca"] = round(time.monotonic() - t3, 1)
    _log(f"Phase 3 done ({time.monotonic()-t3:.1f}s)")

    # Tag discovery source
    for sig in candidates:
        sig["discovery_source"] = discovery_source

    # --- Collect Phase 4 result ---
    holdings_delta = await holdings_task
    phase_timing["phase4_holdings"] = round(time.monotonic() - t4, 1)
    _log(f"Phase 4 done ({time.monotonic()-t4:.1f}s)")

    return candidates, holdings_delta, phase_timing


async def _fetch_holdings(client: NansenClient) -> list[dict[str, Any]]:
    """Fetch smart money holdings (Phase 4 helper for parallel execution)."""
    try:
        holdings_data = await client.get_smart_money_holdings(chains=["solana"])
        return _parse_holdings_delta(holdings_data)
    except Exception as e:
        _log(f"Holdings fetch failed: {e}")
        return []


async def _run_mobula_scan(
    mobula_client: MobulaClient,
    whales: list[str],
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    """Query Mobula whale wallets in parallel, enrich with portfolio data.

    Returns:
        (mobula_signals, phase_timing)
    """
    phase_timing: dict[str, float] = {}
    t0 = time.monotonic()
    _log(f"Mobula: scanning {len(whales)} whales (parallel)...")

    # Query all whales in parallel via asyncio.to_thread
    async def _query_one(wallet: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(mobula_client.get_whale_networth_accum, wallet)

    tasks = [_query_one(w) for w in whales]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    mobula_signals: list[dict[str, Any]] = []
    for data in results:
        if isinstance(data, dict) and data.get('accum_24h_usd', 0) > 10000:
            mobula_signals.append(data)

    phase_timing["mobula_networth"] = round(time.monotonic() - t0, 1)
    _log(f"Mobula networth done: {len(mobula_signals)} accumulating ({time.monotonic()-t0:.1f}s)")

    # Enrich accumulating whales with portfolio (token resolution)
    if mobula_signals:
        t1 = time.monotonic()
        _log(f"Mobula: resolving tokens for {len(mobula_signals)} whales...")

        async def _fetch_portfolio(signal: dict[str, Any]) -> None:
            portfolio = await asyncio.to_thread(
                mobula_client.get_whale_portfolio, signal['wallet']
            )
            if portfolio:
                signal['top_tokens'] = portfolio
                signal['token_mint'] = portfolio[0]['token_mint']
                signal['token_symbol'] = portfolio[0]['token_symbol']

        portfolio_tasks = [_fetch_portfolio(s) for s in mobula_signals]
        await asyncio.gather(*portfolio_tasks, return_exceptions=True)
        phase_timing["mobula_portfolio"] = round(time.monotonic() - t1, 1)
        _log(f"Mobula portfolio done ({time.monotonic()-t1:.1f}s)")

    return mobula_signals, phase_timing


async def _enrich_signals(
    client: NansenClient,
    signals: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Enrich signals with flow intelligence and who bought/sold data (parallel)."""
    if not signals:
        return signals

    async def enrich_one(sig: dict[str, Any]) -> dict[str, Any]:
        mint = sig.get("token_mint", "")
        if not mint:
            return sig

        # Run flow intel + who_bought_sold concurrently
        flow_task = _fetch_flow_intel(client, mint)
        wbs_task = _fetch_buyer_depth(client, mint)
        flow_result, wbs_result = await asyncio.gather(flow_task, wbs_task, return_exceptions=True)

        if isinstance(flow_result, dict):
            sig["flow_intel"] = flow_result
        else:
            sig.setdefault("flow_intel", _empty_flow_intel())

        if isinstance(wbs_result, dict):
            sig["buyer_depth"] = wbs_result
        else:
            sig.setdefault("buyer_depth", _empty_buyer_depth())

        sig.setdefault("dca_count", 0)
        sig.setdefault("discovery_source", "screener")
        return sig

    enriched = await asyncio.gather(*(enrich_one(s) for s in signals), return_exceptions=True)
    return [s for s in enriched if isinstance(s, dict)]


async def _fetch_flow_intel(client: NansenClient, mint: str) -> dict[str, Any]:
    """Fetch and parse flow intelligence for a token."""
    data = await client.get_flow_intelligence(token_address=mint)
    segments = data.get("data", data.get("segments", {}))
    if isinstance(segments, list):
        # Flatten list of segment dicts
        flat: dict[str, float] = {}
        for seg in segments:
            label = seg.get("label", seg.get("type", "")).lower().replace(" ", "_")
            flat[label] = float(seg.get("net_usd", seg.get("net_flow_usd", 0)))
        return {
            "smart_trader_net_usd": flat.get("smart_trader", flat.get("smart_money", 0.0)),
            "whale_net_usd": flat.get("whale", 0.0),
            "exchange_net_usd": flat.get("exchange", 0.0),
            "fresh_wallet_net_usd": flat.get("fresh_wallet", 0.0),
            "top_pnl_net_usd": flat.get("top_pnl", 0.0),
        }
    # Dict-style response
    return {
        "smart_trader_net_usd": float(segments.get("smart_trader_net_usd", segments.get("smart_money_net_usd", 0))),
        "whale_net_usd": float(segments.get("whale_net_usd", 0)),
        "exchange_net_usd": float(segments.get("exchange_net_usd", 0)),
        "fresh_wallet_net_usd": float(segments.get("fresh_wallet_net_usd", 0)),
        "top_pnl_net_usd": float(segments.get("top_pnl_net_usd", 0)),
    }


async def _fetch_buyer_depth(client: NansenClient, mint: str) -> dict[str, Any]:
    """Fetch and parse who bought/sold data for a token."""
    data = await client.get_who_bought_sold(token_address=mint)
    summary = data.get("data", data.get("summary", {}))
    if isinstance(summary, list):
        # Aggregate from list of buyer/seller entries
        sm_buyers = 0
        sm_sellers = 0
        total_buy_vol = 0.0
        total_sell_vol = 0.0
        for entry in summary:
            is_smart = entry.get("is_smart_money", False) or "smart" in entry.get("label", "").lower()
            side = entry.get("side", entry.get("type", "")).lower()
            volume = float(entry.get("volume_usd", entry.get("amount_usd", 0)))
            if side == "buy":
                total_buy_vol += volume
                if is_smart:
                    sm_buyers += 1
            elif side == "sell":
                total_sell_vol += volume
                if is_smart:
                    sm_sellers += 1
        return {
            "smart_money_buyers": sm_buyers,
            "total_buy_volume_usd": total_buy_vol,
            "smart_money_sellers": sm_sellers,
            "total_sell_volume_usd": total_sell_vol,
        }
    # Dict-style response
    return {
        "smart_money_buyers": int(summary.get("smart_money_buyers", 0)),
        "total_buy_volume_usd": float(summary.get("total_buy_volume_usd", 0)),
        "smart_money_sellers": int(summary.get("smart_money_sellers", 0)),
        "total_sell_volume_usd": float(summary.get("total_sell_volume_usd", 0)),
    }


async def _fetch_dca_count(client: NansenClient, mint: str) -> int:
    """Fetch active smart money DCA count for a token."""
    data = await client.get_jupiter_dcas(token_address=mint)
    orders = data.get("data", data.get("orders", []))
    if isinstance(orders, list):
        return len(orders)
    return 0


def _parse_screener_candidates(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse Token Screener response into candidate signals."""
    tokens = data.get("data", data.get("tokens", []))
    if not isinstance(tokens, list):
        return []

    signals = []
    for token in tokens:
        mint = token.get("token_address", token.get("address", ""))
        if not mint:
            continue
        signals.append({
            "token_mint": mint,
            "token_symbol": token.get("symbol", token.get("token_symbol", "UNKNOWN")),
            "wallet_count": int(token.get("smart_money_wallets", token.get("wallet_count", 0))),
            "total_buy_usd": float(token.get("smart_money_inflow_usd", token.get("buy_volume_usd", 0))),
            "confidence": "high" if int(token.get("smart_money_wallets", 0)) >= 5 else "medium",
            "source": "nansen",
        })

    signals.sort(key=lambda s: s["wallet_count"], reverse=True)
    return signals[:10]


def _parse_dex_trades_candidates(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse legacy dex-trades response into candidate signals (fallback)."""
    SOL_MINT = "So11111111111111111111111111111111111111112"
    token_wallets: dict[str, dict[str, Any]] = {}
    transactions = data.get("data", data.get("transactions", []))
    if not isinstance(transactions, list):
        return []

    for tx in transactions:
        token_sold = tx.get("token_sold_address", "")
        token_bought = tx.get("token_bought_address", "")

        if token_sold == SOL_MINT and token_bought != SOL_MINT:
            mint = token_bought
            symbol = tx.get("token_bought_symbol", "UNKNOWN")
            value_usd = tx.get("trade_value_usd", 0)
        elif token_bought == SOL_MINT and token_sold != SOL_MINT:
            continue
        else:
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
                "source": "nansen",
            })

    signals.sort(key=lambda s: s["wallet_count"], reverse=True)
    return signals[:10]


def _parse_holdings_delta(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse smart money holdings into 24h balance changes."""
    holdings = data.get("data", data.get("holdings", []))
    if not isinstance(holdings, list):
        return []

    deltas = []
    for h in holdings:
        change = float(h.get("balance_change_24h", h.get("change_24h", 0)))
        if change > 0:
            deltas.append({
                "token_address": h.get("token_address", h.get("address", "")),
                "token_symbol": h.get("symbol", h.get("token_symbol", "UNKNOWN")),
                "balance_change_24h": change,
            })
    deltas.sort(key=lambda d: d["balance_change_24h"], reverse=True)
    return deltas[:20]


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
        "total_buy_usd": 0.0,
        "confidence": "high" if len(wallets) >= 5 else ("medium" if len(wallets) >= 3 else "low"),
        "source": "nansen",
    }]


def _empty_flow_intel() -> dict[str, float]:
    return {
        "smart_trader_net_usd": 0.0,
        "whale_net_usd": 0.0,
        "exchange_net_usd": 0.0,
        "fresh_wallet_net_usd": 0.0,
        "top_pnl_net_usd": 0.0,
    }


def _empty_buyer_depth() -> dict[str, Any]:
    return {
        "smart_money_buyers": 0,
        "total_buy_volume_usd": 0.0,
        "smart_money_sellers": 0,
        "total_sell_volume_usd": 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Smart Money Oracle")
    parser.add_argument("--token", help="Specific token mint to query")
    args = parser.parse_args()

    result = asyncio.run(query_oracle(args.token))
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] == "OK" else 1)


if __name__ == "__main__":
    main()
