"""Nansen API client — Smart money flows and wallet intelligence.

Used by Smart Money Oracle to detect whale accumulation patterns.
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

    async def close(self) -> None:
        await self._client.close()
