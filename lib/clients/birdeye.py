"""Birdeye API client — Price, liquidity, volume, holder data.

Used by:
- Rug Warden (liquidity check, holder concentration)
- Narrative Hunter (volume anomaly detection, holder delta)
"""

from __future__ import annotations

import os
from typing import Any

from lib.clients.base import BaseClient
from lib.utils.retry import with_retry


class BirdeyeClient:
    """Birdeye Pro: price, liquidity, holders, volume."""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("BIRDEYE_API_KEY", "")
        self._client = BaseClient(
            base_url="https://public-api.birdeye.so",
            headers={
                "X-API-KEY": self.api_key,
                "x-chain": "solana",
            },
            rate_limit=5.0,
            timeout=10.0,
            provider_name="birdeye",
        )

    @with_retry
    async def get_token_overview(self, mint: str) -> dict[str, Any]:
        """Get token overview: price, liquidity, volume, mc, holders."""
        return await self._client.get(
            "/defi/token_overview",
            params={"address": mint},
            cache_ttl=30,
        )

    @with_retry
    async def get_token_security(self, mint: str) -> dict[str, Any]:
        """Get token security info: top holders, mutable authority, etc."""
        return await self._client.get(
            "/defi/token_security",
            params={"address": mint},
            cache_ttl=60,
        )

    @with_retry
    async def get_price(self, mint: str) -> dict[str, Any]:
        """Get current price."""
        return await self._client.get(
            "/defi/price",
            params={"address": mint},
            cache_ttl=15,
        )

    @with_retry
    async def get_price_volume(self, mint: str, timeframe: str = "1h") -> dict[str, Any]:
        """Get price and volume for a timeframe (1h, 4h, 24h)."""
        type_map = {"1h": "1H", "4h": "4H", "24h": "24H"}
        return await self._client.get(
            "/defi/price_volume/single",
            params={"address": mint, "type": type_map.get(timeframe, "1H")},
            cache_ttl=30,
        )

    @with_retry
    async def get_token_list_trending(self, limit: int = 20) -> dict[str, Any]:
        """Get trending tokens by volume."""
        return await self._client.get(
            "/defi/token_trending",
            params={"sort_by": "volume24hUSD", "sort_type": "desc", "limit": limit},
            cache_ttl=60,
        )

    @with_retry
    async def get_new_pairs(self, limit: int = 20, min_liquidity: int = 5000) -> dict[str, Any]:
        """Get recently active small-cap tokens sorted by volume change.

        Uses /defi/tokenlist with v24hChangePercent sort to find tokens
        with the biggest recent volume spikes — targets new/small-cap
        tokens rather than established large-caps.
        """
        return await self._client.get(
            "/defi/tokenlist",
            params={
                "sortBy": "v24hChangePercent",
                "sortType": "desc",
                "minLiquidity": min_liquidity,
                "limit": min(limit, 50),
            },
            cache_ttl=60,
        )

    @with_retry
    async def get_holder_count(self, mint: str) -> dict[str, Any]:
        """Get holder count and recent change."""
        return await self._client.get(
            "/defi/v2/tokens/holder",
            params={"address": mint},
            cache_ttl=60,
        )
    
    @with_retry
    async def get_trades(self, mint: str, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        """Get recent trades for volume concentration analysis."""
        return await self._client.get(
            "/defi/txs/token",
            params={"address": mint, "tx_type": "swap", "limit": limit, "offset": offset},
            cache_ttl=30,
        )

    async def close(self) -> None:
        await self._client.close()
