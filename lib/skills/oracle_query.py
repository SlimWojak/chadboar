"""Smart Money Oracle - CLI entry point.

Queries Nansen Token God Mode (TGM) suite for whale accumulation signals on Solana.
4-phase pipeline: Discovery (dex-trades) → Validation → DCA Detection → Holdings Scan.
Uses /smart-money/dex-trades as primary discovery source (aggregates buy-side trades).

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
from lib.clients.dexscreener import DexScreenerClient, map_dexscreener_to_candidate

# Load environment variables (override=True: always use .env over stale inherited vars)
load_dotenv(override=True)

# Module-level diagnostics collector (reset per query_oracle call)
_diagnostics: list[str] = []

# Module-level source health collector (reset per query_oracle call)
_source_health: dict[str, Any] = {}


WHALE_CACHE_PATH = os.path.join(os.path.dirname(__file__), '../../state/whale_cache.json')


def _log(msg: str) -> None:
    """Print timestamped diagnostic to stderr (visible in heartbeat logs)."""
    ts = time.strftime("%H:%M:%S")
    line = f"[oracle {ts}] {msg}"
    print(line, file=sys.stderr)
    _diagnostics.append(line)


def _load_cached_whales() -> list[str]:
    """Load dynamic whale wallet list from cache (populated by previous heartbeat).

    Returns up to 20 wallet addresses that were seen buying tokens
    in the most recent Nansen dex-trades results. Empty list on first run.
    """
    try:
        with open(WHALE_CACHE_PATH, 'r') as f:
            data = json.load(f)
        wallets = data.get('wallets', [])
        age_hours = (time.time() - data.get('updated_at', 0)) / 3600
        if age_hours > 24:
            _log(f"Whale cache stale ({age_hours:.1f}h old) — treating as empty")
            return []
        _log(f"Loaded {len(wallets)} cached whale wallets ({age_hours:.1f}h old)")
        return wallets[:20]
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_discovered_whales(wallets: list[str]) -> None:
    """Cache discovered whale wallets for use in next heartbeat cycle.

    Extracts unique wallet addresses from Nansen dex-trades and saves
    the top 20 most active (by trade count) to disk.
    """
    try:
        data = {
            'wallets': wallets[:20],
            'count': len(wallets),
            'updated_at': time.time(),
            'updated_iso': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        }
        with open(WHALE_CACHE_PATH, 'w') as f:
            json.dump(data, f, indent=2)
        _log(f"Cached {len(wallets[:20])} whale wallets for next cycle")
    except Exception as e:
        _log(f"Failed to cache whale wallets: {e}")

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

    def get_pulse_listings(self, pulse_url: str, endpoint: str = "/api/2/pulse") -> dict[str, Any]:
        """Fetch Pulse v2 bonding/bonded token listings from Mobula.

        Uses assetMode=true for token-centric flat structure with address,
        symbol, name, liquidity at top level plus organic volume, holder
        categorization, deployer stats, and DexScreener boost flags.
        """
        url = f"{pulse_url}{endpoint}"
        params = {
            'chainId': 'solana:solana',
            'assetMode': 'true',
            'model': 'default',
        }
        resp = requests.get(url, headers=self.headers, params=params, timeout=15)
        if resp.status_code != 200:
            return {}
        return resp.json()

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

    def get_whale_transactions(self, wallet: str) -> list[dict[str, Any]]:
        """Fallback: get wallet's recent Solana transactions to resolve tokens.

        Used when portfolio API returns empty but networth shows accumulation.
        Identifies tokens the whale is buying by scanning recent tx history.
        """
        url = f"{self.base_url}/wallet/trades"
        params = {
            'wallet': wallet,
            'blockchains': 'solana',
            'limit': 50,
        }
        resp = requests.get(url, headers=self.headers, params=params, timeout=15)
        if resp.status_code != 200:
            return []
        data = resp.json()
        txs = data.get('data', data.get('transactions', []))
        if not isinstance(txs, list):
            return []

        # Aggregate buy-side tokens from recent transactions
        SOL_MINT = "So11111111111111111111111111111111111111112"
        STABLES = {"EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
                    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"}  # USDT
        token_buys: dict[str, dict[str, Any]] = {}

        for tx in txs:
            # Look for swap/transfer patterns
            token_in = tx.get('token_in', tx.get('asset_in', {}))
            token_out = tx.get('token_out', tx.get('asset_out', {}))

            # Identify buys: spent SOL/stable, received token
            in_addr = token_in.get('address', '') if isinstance(token_in, dict) else ''
            out_addr = token_out.get('address', '') if isinstance(token_out, dict) else ''

            if in_addr in (SOL_MINT, *STABLES) and out_addr and out_addr not in (SOL_MINT, *STABLES):
                mint = out_addr
                symbol = token_out.get('symbol', 'UNKNOWN') if isinstance(token_out, dict) else 'UNKNOWN'
                value = float(token_out.get('amount_usd', token_out.get('value_usd', 0)) or 0)

                if mint not in token_buys:
                    token_buys[mint] = {'token_mint': mint, 'token_symbol': symbol, 'value_usd': 0.0, 'tx_count': 0}
                token_buys[mint]['value_usd'] += value
                token_buys[mint]['tx_count'] += 1

        # Return tokens sorted by total buy value, minimum $500
        results = [t for t in token_buys.values() if t['value_usd'] >= 500]
        results.sort(key=lambda t: t['value_usd'], reverse=True)
        return results[:5]


async def query_oracle(token_mint: str | None = None) -> dict[str, Any]:
    """Query smart money signals using TGM pipeline with dex-trades fallback."""
    global _diagnostics, _source_health
    _diagnostics = []
    _source_health = {}
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
        pulse_signals: list[dict[str, Any]] = []

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
            pulse_task_idx: int | None = None
            mobula_task_idx: int | None = None

            if 'mobula' in firehose:
                mobula_config = firehose['mobula']
                mobula_client = MobulaClient(mobula_config)
                # Dynamic whale discovery: use wallets cached from previous
                # heartbeat's Nansen dex-trades results. First cycle after
                # boot uses empty list (no whale signals, but Nansen dex-trades
                # already provides token-level discovery).
                whales = _load_cached_whales()
                if whales:
                    mobula_task_idx = len(tasks_to_run)
                    tasks_to_run.append(_run_mobula_scan(mobula_client, whales))
                else:
                    _log("Mobula: no cached whales — skipping wallet scan (will populate from dex-trades)")

                # Pulse scan (Phase 0) — runs in parallel with TGM + Mobula
                pulse_url = mobula_config.get('pulse_url', '')
                pulse_endpoint = mobula_config.get('endpoints', {}).get('pulse', '/api/2/pulse')
                if pulse_url:
                    pulse_task_idx = len(tasks_to_run)
                    tasks_to_run.append(
                        _run_pulse_scan(mobula_client, pulse_url, pulse_endpoint)
                    )

            results = await asyncio.gather(*tasks_to_run, return_exceptions=True)

            # Unpack TGM result (always index 0)
            tgm_result = results[0]
            if isinstance(tgm_result, tuple):
                nansen_signals, holdings_delta, tgm_timing = tgm_result
                phase_timing.update(tgm_timing)
            elif isinstance(tgm_result, Exception):
                _log(f"TGM pipeline FAILED: {tgm_result}")

            # Unpack Mobula result
            if mobula_task_idx is not None and mobula_task_idx < len(results):
                mobula_result = results[mobula_task_idx]
                if isinstance(mobula_result, tuple):
                    mobula_signals, mobula_timing = mobula_result
                    phase_timing.update(mobula_timing)
                elif isinstance(mobula_result, Exception):
                    _log(f"Mobula scan FAILED: {mobula_result}")

            # Unpack Pulse result (Phase 0)
            if pulse_task_idx is not None and pulse_task_idx < len(results):
                pulse_result = results[pulse_task_idx]
                if isinstance(pulse_result, tuple):
                    pulse_signals, pulse_timing = pulse_result
                    phase_timing.update(pulse_timing)
                elif isinstance(pulse_result, Exception):
                    _log(f"Pulse scan FAILED: {pulse_result}")

        all_signals = nansen_signals + mobula_signals + pulse_signals
        phase_timing["total"] = round(time.monotonic() - t_total, 1)
        _log(f"Oracle done: {len(all_signals)} signals ({len(pulse_signals)} pulse) in {phase_timing['total']}s")

        return {
            "status": "OK",
            "nansen_signals": nansen_signals,
            "holdings_delta": holdings_delta,
            "mobula_signals": mobula_signals,
            "pulse_signals": pulse_signals,
            "total_signals": len(all_signals),
            "phase_timing": phase_timing,
            "diagnostics": list(_diagnostics),
            "source_health": dict(_source_health),
        }
    except Exception as e:
        _log(f"Oracle FAILED: {e}")
        return {
            "status": "ERROR",
            "error": str(e),
            "nansen_signals": [],
            "holdings_delta": [],
            "mobula_signals": [],
            "pulse_signals": [],
            "total_signals": 0,
            "phase_timing": phase_timing,
            "diagnostics": list(_diagnostics),
            "source_health": dict(_source_health),
        }
    finally:
        await client.close()


async def _run_tgm_pipeline(client: NansenClient) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, float]]:
    """Run full 4-phase TGM pipeline using dex-trades as primary discovery.

    Returns:
        (nansen_signals, holdings_delta, phase_timing)
    """
    phase_timing: dict[str, float] = {}

    # --- Phase 4: Holdings Scan (start early — doesn't depend on candidates) ---
    t4 = time.monotonic()
    _log("Phase 4: Holdings scan (parallel start)...")
    holdings_task = asyncio.create_task(_fetch_holdings(client))

    # --- Phase 1: Discovery (dex-trades aggregation, primary source) ---
    t1 = time.monotonic()
    candidates: list[dict[str, Any]] = []
    discovery_source = "dex-trades"

    _log("Phase 1: Aggregating smart money dex-trades (limit=100)...")
    try:
        candidates = await _aggregate_dex_trades(client)
        _log(f"Phase 1 done: {len(candidates)} candidates ({time.monotonic()-t1:.1f}s)")
    except Exception as e:
        _log(f"Phase 1 dex-trades FAILED: {e} ({time.monotonic()-t1:.1f}s)")
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
    _source_health["whale_total"] = len(whales)
    _log(f"Mobula: scanning {len(whales)} whales (parallel)...")

    # Query all whales in parallel via asyncio.to_thread
    async def _query_one(wallet: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(mobula_client.get_whale_networth_accum, wallet)

    tasks = [_query_one(w) for w in whales]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    mobula_signals: list[dict[str, Any]] = []
    for data in results:
        if isinstance(data, dict):
            accum = data.get('accum_24h_usd', 0)
            wallet_short = data.get('wallet', '?')[:12]
            if accum > 1000:
                mobula_signals.append(data)
            else:
                _log(f"Whale filtered: {wallet_short}… accum=${accum:,.0f} (need >$1k)")

    _source_health["whale_active"] = len(mobula_signals)
    phase_timing["mobula_networth"] = round(time.monotonic() - t0, 1)
    _log(f"Mobula networth done: {len(mobula_signals)} accumulating ({time.monotonic()-t0:.1f}s)")

    # Enrich accumulating whales with portfolio (token resolution)
    # Fallback: if portfolio returns empty, try recent transactions
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
            else:
                # Fallback: scan recent transactions to resolve tokens
                _log(f"Mobula: portfolio empty for {signal['wallet'][:12]}..., trying tx fallback")
                tx_tokens = await asyncio.to_thread(
                    mobula_client.get_whale_transactions, signal['wallet']
                )
                if tx_tokens:
                    signal['top_tokens'] = tx_tokens
                    signal['token_mint'] = tx_tokens[0]['token_mint']
                    signal['token_symbol'] = tx_tokens[0]['token_symbol']
                    signal['resolution'] = 'tx_fallback'
                    _log(f"Mobula: tx fallback resolved {tx_tokens[0]['token_symbol']} "
                         f"(${tx_tokens[0]['value_usd']:,.0f})")
                else:
                    _log(f"Mobula: tx fallback also empty for {signal['wallet'][:12]}...")

        portfolio_tasks = [_fetch_portfolio(s) for s in mobula_signals]
        await asyncio.gather(*portfolio_tasks, return_exceptions=True)
        phase_timing["mobula_portfolio"] = round(time.monotonic() - t1, 1)
        _log(f"Mobula portfolio done ({time.monotonic()-t1:.1f}s)")

    return mobula_signals, phase_timing


async def _run_pulse_scan(
    mobula_client: MobulaClient,
    pulse_url: str,
    pulse_endpoint: str = "/api/2/pulse",
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    """Fetch Mobula Pulse bonding/bonded tokens and filter candidates.

    Falls back to DexScreener free API when Mobula returns 0 results.

    Returns:
        (pulse_signals, phase_timing)
    """
    phase_timing: dict[str, float] = {}
    t0 = time.monotonic()
    _log("Pulse: fetching bonding/bonded listings...")

    try:
        raw = await asyncio.to_thread(
            mobula_client.get_pulse_listings, pulse_url, pulse_endpoint
        )
    except Exception as e:
        _log(f"Pulse fetch FAILED: {e}")
        _source_health["pulse_error"] = str(e)
        raw = {}

    pulse_signals = _parse_pulse_candidates(raw)
    pulse_raw_count = len(pulse_signals)
    phase_timing["pulse_fetch"] = round(time.monotonic() - t0, 1)
    _log(f"Pulse done: {len(pulse_signals)} candidates ({phase_timing['pulse_fetch']:.1f}s)")

    # DexScreener fallback when Mobula Pulse returns 0 results
    if not pulse_signals:
        t_dex = time.monotonic()
        _log("Pulse empty — falling back to DexScreener...")
        dex_client = DexScreenerClient()
        try:
            dex_raw = await dex_client.get_solana_candidates_enriched()
            _log(f"DexScreener returned {len(dex_raw)} raw Solana candidates")
            pulse_raw_count = len(dex_raw)
            for raw_candidate in dex_raw:
                mapped = map_dexscreener_to_candidate(raw_candidate)
                if mapped is not None:
                    pulse_signals.append(mapped)
            _log(f"DexScreener fallback: {len(pulse_signals)} candidates after filters")
            _source_health["pulse_source"] = "dexscreener"
        except Exception as e:
            _log(f"DexScreener fallback FAILED: {e}")
            _source_health["pulse_error"] = str(e)
        finally:
            await dex_client.close()
        phase_timing["dexscreener_fallback"] = round(time.monotonic() - t_dex, 1)
    else:
        _source_health["pulse_source"] = "mobula"

    _source_health["pulse_raw"] = pulse_raw_count
    _source_health["pulse_filtered"] = len(pulse_signals)

    return pulse_signals, phase_timing


def _parse_pulse_candidates(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse Pulse v2 response into scored candidate signals.

    Pulse returns: {bonded: {data: [...]}, bonding: {data: [...]}, new: {data: [...]}}
    Each item in assetMode=true is a flat token object with address, symbol,
    name, liquidity, volume_24h, organic_volume_24h, holder breakdowns, etc.

    Filters:
    - liquidity > $5k
    - volume > $1k
    """
    candidates: list[dict[str, Any]] = []
    if not isinstance(raw, dict):
        return []

    # Process bonded tokens (highest value — just migrated to Raydium)
    bonded_section = raw.get("bonded", {})
    bonded = bonded_section.get("data", []) if isinstance(bonded_section, dict) else bonded_section
    if not isinstance(bonded, list):
        bonded = []

    for token in bonded:
        candidate = _extract_pulse_token(token, stage="bonded")
        if candidate:
            candidates.append(candidate)

    # Also check bonding tokens (still on curve, but interesting)
    bonding_section = raw.get("bonding", {})
    bonding = bonding_section.get("data", []) if isinstance(bonding_section, dict) else bonding_section
    if not isinstance(bonding, list):
        bonding = []

    for token in bonding:
        candidate = _extract_pulse_token(token, stage="bonding")
        if candidate:
            candidates.append(candidate)

    # Sort by organic volume ratio × pro_trader_pct (quality signal)
    candidates.sort(
        key=lambda c: c.get("pulse_organic_ratio", 0) * c.get("pulse_pro_trader_pct", 0),
        reverse=True,
    )
    return candidates[:10]


def _extract_pulse_token(token: dict[str, Any], stage: str) -> dict[str, Any] | None:
    """Extract and filter a single Pulse token entry.

    assetMode=true fields: address, symbol, name, liquidity, volume_24h,
    organic_volume_24h, bundlersHoldings, snipersHoldings, proTradersHoldings,
    smartTradersHoldingsPercentage, holdersCount, deployerMigrationsCount,
    deployer, socials:{twitter,website,telegram}, trendingScore1h,
    dexscreenerBoosted, dexscreenerAdPaid, market_cap, etc.
    """
    mint = token.get("address", "")
    if not mint:
        return None

    symbol = token.get("symbol", token.get("tokenSymbol", "UNKNOWN"))
    name = token.get("name", token.get("tokenName", ""))
    liquidity = float(token.get("liquidity", 0))
    volume = float(token.get("volume_24h", 0))

    # Hard filters
    if liquidity < 5000:
        return None
    if volume < 1000:
        return None

    # Holder categorization (assetMode=true returns raw values, not percentages)
    bundler_pct = float(token.get("bundlersHoldings", 0))
    sniper_pct = float(token.get("snipersHoldings", 0))
    pro_trader_pct = float(token.get("proTradersHoldings", 0))
    smart_trader_pct = float(token.get("smartTradersHoldingsPercentage", 0))

    # Organic volume ratio
    organic_vol = float(token.get("organic_volume_24h", volume))
    organic_ratio = round(organic_vol / volume, 3) if volume > 0 else 0.0

    # Quality flags (passed through to scoring — no longer hard rejections)
    # Scoring applies penalties: bundler >20% (-10), sniper >30% (-10), organic <0.3 (-10)

    # Ghost metadata detection (no socials but volume exists)
    socials = token.get("socials", {}) or {}
    has_socials = bool(socials.get("twitter") or socials.get("website") or socials.get("telegram"))
    ghost_metadata = not has_socials and volume > 5000

    # Deployer migration count
    deployer_migrations = int(token.get("deployerMigrationsCount", token.get("deployerMigrations", 0)))

    # Extra signals from Pulse (pass through for scoring/logging)
    holders_count = int(token.get("holdersCount", token.get("holders_count", 0)))
    trending_score = float(token.get("trendingScore1h", 0))
    dexscreener_boosted = bool(token.get("dexscreenerBoosted", False))
    market_cap = float(token.get("marketCap", token.get("market_cap", 0)))

    return {
        "token_mint": mint,
        "token_symbol": symbol,
        "token_name": name,
        "source": "pulse",
        "discovery_source": f"pulse-{stage}",
        "pulse_stage": stage,
        "liquidity_usd": round(liquidity, 2),
        "volume_usd": round(volume, 2),
        "market_cap_usd": round(market_cap, 2),
        "holders_count": holders_count,
        "pulse_organic_ratio": organic_ratio,
        "pulse_bundler_pct": round(bundler_pct, 2),
        "pulse_sniper_pct": round(sniper_pct, 2),
        "pulse_pro_trader_pct": round(pro_trader_pct + smart_trader_pct, 2),
        "pulse_ghost_metadata": ghost_metadata,
        "pulse_deployer_migrations": deployer_migrations,
        "pulse_trending_score": trending_score,
        "pulse_dexscreener_boosted": dexscreener_boosted,
        "wallet_count": 0,
        "total_buy_usd": round(volume, 2),
        "confidence": "medium" if stage == "bonded" else "low",
        "flow_intel": _empty_flow_intel(),
        "buyer_depth": _empty_buyer_depth(),
        "dca_count": 0,
    }


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
    """Parse Token Screener response into candidate signals.

    Client-side filters (Nansen screener doesn't support server-side):
    - min 1 smart money wallet
    - max $50M market cap
    Sorted by smart money inflow descending.
    """
    tokens = data.get("data", data.get("tokens", []))
    if not isinstance(tokens, list):
        return []

    signals = []
    for token in tokens:
        mint = token.get("token_address", token.get("address", ""))
        if not mint:
            continue
        wallet_count = int(token.get("smart_money_wallets", token.get("wallet_count", 0)))
        mcap = float(token.get("market_cap", token.get("mc", 0)) or 0)
        # Filter: at least 1 SM wallet, max $50M mcap (0 = unknown, allow through)
        if wallet_count < 1:
            continue
        if mcap > 50_000_000:
            continue
        signals.append({
            "token_mint": mint,
            "token_symbol": token.get("symbol", token.get("token_symbol", "UNKNOWN")),
            "wallet_count": wallet_count,
            "total_buy_usd": float(token.get("smart_money_inflow_usd", token.get("buy_volume_usd", 0))),
            "confidence": "high" if wallet_count >= 5 else "medium",
            "source": "nansen",
        })

    # Sort by SM inflow (what we wanted from order_by but can't server-side)
    signals.sort(key=lambda s: s["total_buy_usd"], reverse=True)
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


async def _aggregate_dex_trades(client: NansenClient) -> list[dict[str, Any]]:
    """Primary discovery: aggregate smart money dex-trades into accumulation candidates.

    Fetches recent dex-trades (limit=100), groups by token BOUGHT (accumulation signal),
    and returns top 5 candidates sorted by smart money wallet count then inflow USD.

    Returns candidates in the same format as _parse_screener_candidates:
        token_mint, token_symbol, wallet_count, total_buy_usd, confidence, source
    """
    try:
        data = await client.get_smart_money_transactions(limit=100)
    except Exception as e:
        _source_health["nansen_error"] = str(e)
        _source_health["nansen_raw_trades"] = 0
        _source_health["nansen_candidates"] = 0
        raise
    transactions = data.get("data", data.get("transactions", []))
    if not isinstance(transactions, list):
        _log(f"_aggregate_dex_trades: no transactions list in response (keys={list(data.keys())})")
        _source_health["nansen_raw_trades"] = 0
        _source_health["nansen_candidates"] = 0
        return []

    _source_health["nansen_raw_trades"] = len(transactions)
    _log(f"_aggregate_dex_trades: processing {len(transactions)} raw trades")

    # Group by token_bought_address (accumulation = buying)
    token_agg: dict[str, dict[str, Any]] = {}
    for tx in transactions:
        mint = tx.get("token_bought_address", "")
        if not mint:
            continue

        wallet = tx.get("trader_address", "")
        if not wallet:
            continue

        symbol = tx.get("token_bought_symbol", "UNKNOWN")
        value_usd = float(tx.get("trade_value_usd", 0) or 0)
        mcap = float(tx.get("token_bought_market_cap", 0) or 0)

        if mint not in token_agg:
            token_agg[mint] = {
                "token_mint": mint,
                "token_symbol": symbol,
                "wallets": set(),
                "total_inflow_usd": 0.0,
                "market_cap": mcap,
            }

        token_agg[mint]["wallets"].add(wallet)
        token_agg[mint]["total_inflow_usd"] += value_usd
        # Keep the latest non-zero market_cap
        if mcap > 0:
            token_agg[mint]["market_cap"] = mcap

    # Filter: smart_money_wallets >= 1, market_cap < $50M (or market_cap == 0 = unknown)
    filtered = []
    for info in token_agg.values():
        wallet_count = len(info["wallets"])
        mcap = info["market_cap"]

        if wallet_count < 1:
            continue
        if mcap > 50_000_000:
            continue

        confidence = "high" if wallet_count >= 5 else "medium" if wallet_count >= 3 else "low"
        filtered.append({
            "token_mint": info["token_mint"],
            "token_symbol": info["token_symbol"],
            "wallet_count": wallet_count,
            "total_buy_usd": round(info["total_inflow_usd"], 2),
            "confidence": confidence,
            "source": "nansen",
            "market_cap_usd": round(info.get("market_cap", 0), 2),
        })

    # Sort by smart_money_wallets DESC, then total_inflow_usd DESC
    filtered.sort(key=lambda s: (s["wallet_count"], s["total_buy_usd"]), reverse=True)

    _source_health["nansen_candidates"] = len(filtered)
    _log(f"_aggregate_dex_trades: {len(filtered)} tokens after filters, returning top 5")

    # Extract and cache discovered wallet addresses for Mobula whale tracking.
    # Sort by trade count (most active wallets first), deduplicate.
    wallet_counts: dict[str, int] = {}
    for info in token_agg.values():
        for w in info["wallets"]:
            wallet_counts[w] = wallet_counts.get(w, 0) + 1
    discovered_wallets = sorted(wallet_counts.keys(), key=lambda w: wallet_counts[w], reverse=True)
    if discovered_wallets:
        _save_discovered_whales(discovered_wallets)
        _source_health["whales_discovered"] = len(discovered_wallets)

    return filtered[:5]


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
