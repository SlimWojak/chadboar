"""DexScreener API client — Free, no-auth token discovery.

Endpoints:
- Token boosts (top/v1): freshly boosted tokens (paid promotions, but signals attention)
- Token profiles (latest/v1): new token profiles (recently listed)
- Dex search: search for recent PumpFun pairs on Solana

Used as a fallback for Mobula Pulse when it returns empty data.
Provides graduation play-type candidates (PumpFun -> Raydium migrations).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx


class DexScreenerClient:
    """DexScreener free API — no auth required.

    Rate limit: ~60 req/min (undocumented but generous for free tier).
    All methods are async and return raw JSON responses.
    """

    BASE_URL = "https://api.dexscreener.com"

    def __init__(self, timeout: float = 12.0):
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            headers={
                "Accept": "application/json",
                "User-Agent": "ChadBoar/1.0",
            },
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def get_boosted_tokens(self) -> list[dict[str, Any]]:
        """GET /token-boosts/top/v1 — freshly boosted tokens.

        Returns list of boosted token entries with:
        - tokenAddress, chainId, icon, description, links, amount, totalAmount
        """
        resp = await self._client.get(f"{self.BASE_URL}/token-boosts/top/v1")
        resp.raise_for_status()
        data = resp.json()
        # Response is a top-level list
        if isinstance(data, list):
            return data
        return data.get("data", data.get("tokens", []))

    async def get_latest_profiles(self) -> list[dict[str, Any]]:
        """GET /token-profiles/latest/v1 — new token profiles.

        Returns list of recently created token profiles with:
        - tokenAddress, chainId, icon, description, links
        """
        resp = await self._client.get(f"{self.BASE_URL}/token-profiles/latest/v1")
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        return data.get("data", data.get("tokens", []))

    async def search_pairs(self, query: str = "pumpfun") -> list[dict[str, Any]]:
        """GET /latest/dex/search?q=<query> — search for DEX pairs.

        Returns pairs with full market data:
        - baseToken (address, name, symbol), quoteToken
        - liquidity.usd, volume.h24, priceChange (h1, h6, h24)
        - chainId, dexId, pairAddress, url
        """
        resp = await self._client.get(
            f"{self.BASE_URL}/latest/dex/search",
            params={"q": query},
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("pairs", [])

    async def get_token_pairs(self, chain: str, token_address: str) -> list[dict[str, Any]]:
        """GET /tokens/v1/{chain}/{tokenAddress} — get pairs for a specific token.

        Returns pairs with full market data for a known token address.
        Useful for enriching boosted/profile tokens with market data.
        """
        resp = await self._client.get(
            f"{self.BASE_URL}/tokens/v1/{chain}/{token_address}",
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        return data.get("pairs", data.get("data", []))

    async def get_solana_candidates(self) -> list[dict[str, Any]]:
        """Aggregate DexScreener sources and return Solana-only candidates.

        Combines boosted tokens, latest profiles, and PumpFun search results.
        Filters for Solana chain and deduplicates by token address.
        Returns raw DexScreener data (not yet mapped to candidate format).
        """
        # Run all three endpoints in parallel
        boosted_task = self.get_boosted_tokens()
        profiles_task = self.get_latest_profiles()
        search_task = self.search_pairs("pumpfun")

        results = await asyncio.gather(
            boosted_task, profiles_task, search_task,
            return_exceptions=True,
        )

        boosted = results[0] if isinstance(results[0], list) else []
        profiles = results[1] if isinstance(results[1], list) else []
        search_pairs = results[2] if isinstance(results[2], list) else []

        # Collect token addresses that have boost/profile activity on Solana
        solana_token_addrs: dict[str, dict[str, Any]] = {}

        # Process boosted tokens — filter for Solana
        for token in boosted:
            chain = token.get("chainId", "")
            if chain != "solana":
                continue
            addr = token.get("tokenAddress", "")
            if not addr:
                continue
            if addr not in solana_token_addrs:
                solana_token_addrs[addr] = {
                    "tokenAddress": addr,
                    "source_flags": set(),
                    "boost_amount": 0,
                    "description": token.get("description", ""),
                    "links": token.get("links", []),
                }
            solana_token_addrs[addr]["source_flags"].add("boosted")
            solana_token_addrs[addr]["boost_amount"] += int(token.get("totalAmount", token.get("amount", 0)))

        # Process latest profiles — filter for Solana
        for token in profiles:
            chain = token.get("chainId", "")
            if chain != "solana":
                continue
            addr = token.get("tokenAddress", "")
            if not addr:
                continue
            if addr not in solana_token_addrs:
                solana_token_addrs[addr] = {
                    "tokenAddress": addr,
                    "source_flags": set(),
                    "boost_amount": 0,
                    "description": token.get("description", ""),
                    "links": token.get("links", []),
                }
            solana_token_addrs[addr]["source_flags"].add("profile")
            # Merge links if available
            if token.get("links"):
                solana_token_addrs[addr]["links"] = token["links"]

        # Process search pairs — filter for Solana, extract market data
        pair_data: dict[str, dict[str, Any]] = {}
        for pair in search_pairs:
            chain = pair.get("chainId", "")
            if chain != "solana":
                continue
            base_token = pair.get("baseToken", {})
            addr = base_token.get("address", "")
            if not addr:
                continue
            # Keep the pair with highest liquidity for each token
            existing = pair_data.get(addr)
            pair_liq = float((pair.get("liquidity") or {}).get("usd", 0))
            if existing is None or pair_liq > float((existing.get("liquidity") or {}).get("usd", 0)):
                pair_data[addr] = pair

        # Now enrich boosted/profile tokens with pair market data if available
        # Also include search-only tokens that pass filters
        candidates: list[dict[str, Any]] = []
        seen_addrs: set[str] = set()

        # First: tokens with boosted/profile flags that have pair data
        for addr, meta in solana_token_addrs.items():
            seen_addrs.add(addr)
            pair = pair_data.get(addr)
            entry: dict[str, Any] = {
                "tokenAddress": addr,
                "source_flags": list(meta["source_flags"]),
                "boost_amount": meta["boost_amount"],
                "description": meta.get("description", ""),
                "links": meta.get("links", []),
            }
            if pair:
                entry.update(_extract_pair_market_data(pair))
            candidates.append(entry)

        # Second: search-only pairs not already included
        for addr, pair in pair_data.items():
            if addr in seen_addrs:
                continue
            seen_addrs.add(addr)
            entry = {
                "tokenAddress": addr,
                "source_flags": ["search"],
                "boost_amount": 0,
                "description": "",
                "links": [],
            }
            entry.update(_extract_pair_market_data(pair))
            candidates.append(entry)

        return candidates

    async def get_solana_candidates_enriched(self) -> list[dict[str, Any]]:
        """Get Solana candidates and enrich any missing market data.

        For boosted/profile tokens that had no search pair data,
        fetches pair data individually via get_token_pairs.
        This ensures we have liquidity/volume for filtering.
        """
        candidates = await self.get_solana_candidates()

        # Find candidates missing market data (no liquidity field)
        needs_enrichment = [
            c for c in candidates
            if "liquidity_usd" not in c and c.get("tokenAddress")
        ]

        if needs_enrichment:
            # Batch enrich — limit to 5 to avoid rate limits
            async def _enrich_one(candidate: dict[str, Any]) -> None:
                try:
                    pairs = await self.get_token_pairs("solana", candidate["tokenAddress"])
                    if pairs:
                        # Pick pair with highest liquidity
                        best = max(pairs, key=lambda p: float((p.get("liquidity") or {}).get("usd", 0)))
                        candidate.update(_extract_pair_market_data(best))
                except Exception:
                    pass  # Skip enrichment failures silently

            tasks = [_enrich_one(c) for c in needs_enrichment[:5]]
            await asyncio.gather(*tasks, return_exceptions=True)

        return candidates


def _extract_pair_market_data(pair: dict[str, Any]) -> dict[str, Any]:
    """Extract market data fields from a DexScreener pair object."""
    base_token = pair.get("baseToken", {})
    liquidity = pair.get("liquidity") or {}
    volume = pair.get("volume") or {}
    price_change = pair.get("priceChange") or {}

    return {
        "token_symbol": base_token.get("symbol", base_token.get("name", "UNKNOWN")),
        "token_name": base_token.get("name", ""),
        "liquidity_usd": float(liquidity.get("usd", 0)),
        "volume_24h": float(volume.get("h24", 0)),
        "volume_6h": float(volume.get("h6", 0)),
        "volume_1h": float(volume.get("h1", 0)),
        "price_change_1h": float(price_change.get("h1", 0)),
        "price_change_6h": float(price_change.get("h6", 0)),
        "price_change_24h": float(price_change.get("h24", 0)),
        "pair_address": pair.get("pairAddress", ""),
        "dex_id": pair.get("dexId", ""),
        "pair_url": pair.get("url", ""),
        "pair_created_at": pair.get("pairCreatedAt", ""),
        "fdv": float(pair.get("fdv", 0)),
        "market_cap": float(pair.get("marketCap", pair.get("mc", 0)) or 0),
    }


def map_dexscreener_to_candidate(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Map a DexScreener raw candidate to the Pulse candidate format.

    Applies the same filters as Mobula Pulse:
    - liquidity > $5K
    - volume > $1K

    Returns None if the candidate doesn't pass filters.
    """
    addr = raw.get("tokenAddress", "")
    if not addr:
        return None

    liquidity = float(raw.get("liquidity_usd", 0))
    volume = float(raw.get("volume_24h", 0))

    # Hard filters (same as Mobula Pulse)
    if liquidity < 5000:
        return None
    if volume < 1000:
        return None

    symbol = raw.get("token_symbol", "UNKNOWN")

    # Ghost metadata: no social links but has volume
    links = raw.get("links", [])
    has_socials = False
    if isinstance(links, list):
        for link in links:
            link_type = ""
            if isinstance(link, dict):
                link_type = link.get("type", link.get("label", "")).lower()
            elif isinstance(link, str):
                link_type = link.lower()
            if any(s in link_type for s in ("twitter", "telegram", "website", "discord")):
                has_socials = True
                break
    ghost_metadata = not has_socials and volume > 5000

    # Determine stage based on DEX:
    # - raydium/orca/meteora = "bonded" (graduated from PumpFun to a real DEX)
    # - pumpswap/pumpfun = "bonding" (still on PumpFun's native AMM)
    # - anything else with liquidity > $10k = "bonded" (likely graduated somewhere)
    dex_id = raw.get("dex_id", "")
    graduated_dexes = ("raydium", "raydium-clmm", "raydium-cp", "orca", "meteora")
    bonding_dexes = ("pumpswap", "pumpfun")
    if dex_id in graduated_dexes:
        stage = "bonded"
    elif dex_id in bonding_dexes:
        stage = "bonding"
    elif liquidity > 10000:
        stage = "bonded"
    else:
        stage = "bonding"

    source_flags = raw.get("source_flags", [])
    boost_amount = int(raw.get("boost_amount", 0))
    market_cap = float(raw.get("market_cap", raw.get("fdv", 0)))

    # Map DexScreener boost to the scoring field
    dexscreener_boosted = boost_amount > 0 or "boosted" in source_flags

    # Use 1h price change as a proxy trending score (DexScreener doesn't have Mobula's trendingScore)
    price_change_1h = float(raw.get("price_change_1h", 0))
    trending_score = abs(price_change_1h) * 5 if abs(price_change_1h) > 20 else 0.0

    return {
        "token_mint": addr,
        "token_symbol": symbol,
        "source": "dexscreener",
        "discovery_source": f"dexscreener-{'+'.join(source_flags) if source_flags else 'search'}",
        "pulse_stage": stage,
        "liquidity_usd": round(liquidity, 2),
        "volume_usd": round(volume, 2),
        "market_cap_usd": round(market_cap, 2),
        # DexScreener doesn't provide holder categorization — use safe defaults
        "pulse_organic_ratio": 0.5,   # Unknown, neutral assumption
        "pulse_bundler_pct": 0.0,     # Unknown
        "pulse_sniper_pct": 0.0,      # Unknown
        "pulse_pro_trader_pct": 0.0,  # Unknown
        "pulse_ghost_metadata": ghost_metadata,
        "pulse_deployer_migrations": 0,  # Unknown
        "pulse_trending_score": trending_score,
        "pulse_dexscreener_boosted": dexscreener_boosted,
        # Market data from DexScreener
        "price_change_1h": price_change_1h,
        "price_change_24h": raw.get("price_change_24h", 0),
        "fdv": raw.get("fdv", 0),
        "dex_id": dex_id,
        "pair_url": raw.get("pair_url", ""),
        # Standard fields for scorer compatibility
        "wallet_count": 0,
        "total_buy_usd": round(volume, 2),
        "confidence": "medium" if stage == "bonded" else "low",
        "flow_intel": {
            "smart_trader_net_usd": 0.0,
            "whale_net_usd": 0.0,
            "exchange_net_usd": 0.0,
            "fresh_wallet_net_usd": 0.0,
            "top_pnl_net_usd": 0.0,
        },
        "buyer_depth": {
            "smart_money_buyers": 0,
            "total_buy_volume_usd": 0.0,
            "smart_money_sellers": 0,
            "total_sell_volume_usd": 0.0,
        },
        "dca_count": 0,
    }
