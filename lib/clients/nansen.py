"""Nansen API client — Smart money flows and wallet intelligence.

Used by Smart Money Oracle to detect whale accumulation patterns.
Full Token God Mode (TGM) suite for discovery, flow analysis, and holder intel.
"""

from __future__ import annotations

import os
from typing import Any

from lib.clients.base import BaseClient
from lib.utils.retry import with_retry


class NansenClient:
    """Nansen Pro: smart money flows, wallet PnL, entity labels."""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("NANSEN_API_KEY", "")
        self._client = BaseClient(
            base_url="https://api.nansen.ai/api/v1",
            headers={
                "apiKey": self.api_key,
                "Content-Type": "application/json",
            },
            rate_limit=2.0,
            timeout=15.0,
            provider_name="nansen",
        )

    @with_retry
    async def get_smart_money_transactions(
        self,
        chain: str = "solana",
        limit: int = 50,
    ) -> dict[str, Any]:
        """Get recent smart money DEX trades on Solana."""
        body = {
            "chains": [chain],
            "pagination": {"page": 1, "per_page": limit},
            "order_by": [{"field": "block_timestamp", "direction": "DESC"}],
        }
        return await self._client.post(
            "/smart-money/dex-trades",
            json_data=body,
        )

    @with_retry
    async def get_token_smart_money(self, mint: str) -> dict[str, Any]:
        """Get smart money netflow for a specific token."""
        body = {
            "chains": ["solana"],
            "filters": {
                "token_address": [mint],
            },
            "pagination": {"page": 1, "per_page": 100},
        }
        return await self._client.post(
            "/smart-money/netflow",
            json_data=body,
        )

    @with_retry
    async def get_wallet_profile(self, address: str) -> dict[str, Any]:
        """Get wallet labels (Nansen Profiler API)."""
        body = {"chains": ["solana"], "address": address}
        return await self._client.post(
            "/profiler/labels",
            json_data=body,
        )

    async def get_wallet_tokens(self, address: str) -> dict[str, Any]:
        """Get tokens held by a wallet (Nansen Profiler API)."""
        body = {"chains": ["solana"], "address": address}
        return await self._client.post(
            "/profiler/holdings",
            json_data=body,
        )
    
    @with_retry
    async def get_wallet_transaction_history(
        self, 
        address: str, 
        limit: int = 100,
        days: int = 7
    ) -> dict[str, Any]:
        """Get wallet transaction history for dumper detection."""
        body = {
            "chains": ["solana"],
            "address": address,
            "pagination": {"page": 1, "per_page": limit},
            "order_by": [{"field": "block_timestamp", "direction": "DESC"}],
        }
        return await self._client.post(
            "/profiler/transactions",
            json_data=body,
        )

    # --- Token God Mode (TGM) endpoints ---

    @with_retry
    async def screen_tokens(
        self,
        chains: list[str] | None = None,
        timeframe: str = "1h",
        filters: dict[str, Any] | None = None,
        order_by: list[dict[str, str]] | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Token Screener — filter tokens by smart money inflows, volume, liquidity (5 credits)."""
        body: dict[str, Any] = {
            "chains": chains or ["solana"],
            "timeframe": timeframe,
            "pagination": {"page": 1, "per_page": limit},
        }
        if filters:
            body["filters"] = filters
        if order_by:
            body["order_by"] = order_by
        else:
            body["order_by"] = [{"field": "volume", "direction": "DESC"}]
        return await self._client.post(
            "/token-screener",
            json_data=body,
        )

    @with_retry
    async def get_flow_intelligence(
        self,
        chain: str = "solana",
        token_address: str = "",
        timeframe: str = "1h",
    ) -> dict[str, Any]:
        """Flow Intelligence — segment breakdown: exchange/whale/smart_trader/fresh_wallet/top_pnl (1 credit)."""
        body = {
            "chain": chain,
            "token_address": token_address,
            "timeframe": timeframe,
        }
        return await self._client.post(
            "/tgm/flow-intelligence",
            json_data=body,
        )

    @with_retry
    async def get_who_bought_sold(
        self,
        chain: str = "solana",
        token_address: str = "",
        date_range: str = "1d",
        buy_or_sell: str = "all",
    ) -> dict[str, Any]:
        """Who Bought/Sold — aggregated buyer/seller volumes with smart money labels (1 credit)."""
        body: dict[str, Any] = {
            "chain": chain,
            "token_address": token_address,
            "date_range": date_range,
        }
        if buy_or_sell != "all":
            body["buy_or_sell"] = buy_or_sell
        return await self._client.post(
            "/tgm/who-bought-sold",
            json_data=body,
        )

    @with_retry
    async def get_jupiter_dcas(
        self,
        token_address: str = "",
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Jupiter DCAs — active DCA orders from smart money on Jupiter, Solana-only (1-5 credits)."""
        body: dict[str, Any] = {
            "token_address": token_address,
        }
        if filters:
            body["filters"] = filters
        return await self._client.post(
            "/tgm/jup-dca",
            json_data=body,
        )

    @with_retry
    async def get_smart_money_holdings(
        self,
        chains: list[str] | None = None,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Smart Money Holdings — aggregated smart money token balances with 24h changes (5 credits)."""
        body: dict[str, Any] = {
            "chains": chains or ["solana"],
            "pagination": {"page": 1, "per_page": 100},
        }
        if filters:
            body["filters"] = filters
        return await self._client.post(
            "/smart-money/holdings",
            json_data=body,
        )

    @with_retry
    async def get_tgm_holders(
        self,
        chain: str = "solana",
        token_address: str = "",
        label_type: str = "smart_money",
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """TGM Holders — token holders filtered by whale/smart_money/exchange/fresh_wallet (5 credits)."""
        body: dict[str, Any] = {
            "chain": chain,
            "token_address": token_address,
            "label_type": label_type,
        }
        if filters:
            body["filters"] = filters
        return await self._client.post(
            "/tgm/holders",
            json_data=body,
        )

    @with_retry
    async def get_pnl_leaderboard(
        self,
        chain: str = "solana",
        token_address: str = "",
        date_range: str = "1d",
    ) -> dict[str, Any]:
        """PnL Leaderboard — top profitable traders for a specific token (5 credits)."""
        body = {
            "chain": chain,
            "token_address": token_address,
            "date_range": date_range,
        }
        return await self._client.post(
            "/tgm/pnl-leaderboard",
            json_data=body,
        )

    async def close(self) -> None:
        await self._client.close()
