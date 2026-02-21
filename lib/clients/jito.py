"""Jito SDK client — MEV-protected bundle submission.

Submits signed transactions via Jito bundles to prevent sandwich attacks.
Used by Blind Executioner after the signer returns a signed transaction.
"""

from __future__ import annotations

import os
from typing import Any

from lib.clients.base import BaseClient


class JitoClient:
    """Jito Block Engine: MEV-protected bundle submission."""

    def __init__(self):
        self._client = BaseClient(
            base_url="https://mainnet.block-engine.jito.wtf",
            rate_limit=5.0,
            timeout=15.0,
            provider_name="jito",
        )

    async def send_bundle(self, signed_transactions: list[str]) -> dict[str, Any]:
        """Submit a bundle of signed transactions.

        Args:
            signed_transactions: List of base64-encoded signed transactions.

        Returns:
            Bundle ID and status.
        """
        return await self._client.post(
            "/api/v1/bundles",
            json_data={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendBundle",
                "params": [signed_transactions, {"encoding": "base64"}],
            },
        )

    async def get_bundle_statuses(self, bundle_ids: list[str]) -> dict[str, Any]:
        """Check status of submitted bundles."""
        return await self._client.post(
            "/api/v1/bundles",
            json_data={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getBundleStatuses",
                "params": [bundle_ids],
            },
        )

    async def get_tip_accounts(self) -> dict[str, Any]:
        """Get current Jito tip accounts."""
        return await self._client.post(
            "/api/v1/bundles",
            json_data={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTipAccounts",
                "params": [],
            },
        )

    async def close(self) -> None:
        await self._client.close()
