"""Helius API client — Solana RPC + Enhanced APIs.

Provides:
- Solana RPC calls (with fallback chain)
- Token metadata
- Transaction simulation (honeypot detection)
- Recent pool detection (Pump.fun/Raydium)
"""

from __future__ import annotations

import os
from typing import Any

from lib.clients.base import BaseClient, RPCFallbackClient


class HeliusClient:
    """Helius Developer tier: RPC + Enhanced APIs."""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("HELIUS_API_KEY", "")
        self._api = BaseClient(
            base_url="https://api.helius.xyz/v0",
            rate_limit=10.0,
            timeout=10.0,
            provider_name="helius",
        )
        self._rpc = RPCFallbackClient([
            {
                "provider": "helius",
                "url": f"https://mainnet.helius-rpc.com/?api-key={self.api_key}",
                "rate_limit": 10.0,
                "timeout_seconds": 10,
            },
            {
                "provider": "public",
                "url": "https://api.mainnet-beta.solana.com",
                "rate_limit": 5.0,
                "timeout_seconds": 20,
            },
        ])

    async def get_token_metadata(self, mint: str) -> dict[str, Any]:
        """Get token metadata (name, symbol, decimals, authority)."""
        return await self._api.post(
            f"/token-metadata?api-key={self.api_key}",
            json_data={"mintAccounts": [mint], "includeOffChain": True},
        )

    async def get_token_holders(self, mint: str, limit: int = 20) -> dict[str, Any]:
        """Get top token holders."""
        return await self._api.get(
            f"/token-holders?api-key={self.api_key}",
            params={"mint": mint, "limit": limit},
        )

    async def simulate_transaction(self, tx_base64: str) -> dict[str, Any]:
        """Simulate a transaction (for honeypot detection)."""
        return await self._rpc.request(
            "POST",
            json_data={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "simulateTransaction",
                "params": [tx_base64, {"encoding": "base64"}],
            },
        )

    async def get_recent_transactions(
        self, address: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Get recent transactions for an address."""
        result = await self._api.get(
            f"/addresses/{address}/transactions?api-key={self.api_key}",
            params={"limit": limit},
        )
        return result if isinstance(result, list) else []

    async def get_account_info(self, address: str) -> dict[str, Any]:
        """RPC getAccountInfo with fallback chain."""
        return await self._rpc.request(
            "POST",
            json_data={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAccountInfo",
                "params": [address, {"encoding": "jsonParsed"}],
            },
        )

    async def get_recent_slot_fees(self) -> dict[str, Any]:
        """Get recent priority fees for dynamic tip calculation."""
        return await self._rpc.request(
            "POST",
            json_data={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getRecentPrioritizationFees",
                "params": [],
            },
        )

    async def close(self) -> None:
        await self._api.close()
        await self._rpc.close()
