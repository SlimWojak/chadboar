"""Jupiter API client — Swap quotes and route construction.

Used by Blind Executioner for trade execution on Solana.
Jupiter is the main DEX aggregator.
"""

from __future__ import annotations

import os
from typing import Any

from lib.clients.base import BaseClient

# SOL mint address
SOL_MINT = "So11111111111111111111111111111111111111112"


class JupiterClient:
    """Jupiter v6 API: quotes, swap transactions."""

    def __init__(self):
        self._client = BaseClient(
            base_url="https://quote-api.jup.ag/v6",
            rate_limit=10.0,
            timeout=10.0,
            provider_name="jupiter",
        )

    async def get_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        slippage_bps: int = 300,
    ) -> dict[str, Any]:
        """Get swap quote with best route.

        Args:
            input_mint: Token mint to sell
            output_mint: Token mint to buy
            amount: Amount in smallest unit (lamports for SOL)
            slippage_bps: Max slippage in basis points (300 = 3%)
        """
        return await self._client.get(
            "/quote",
            params={
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": str(amount),
                "slippageBps": slippage_bps,
            },
        )

    async def get_swap_transaction(
        self,
        quote_response: dict[str, Any],
        user_public_key: str,
        priority_fee_lamports: int = 5000,
    ) -> dict[str, Any]:
        """Get serialized swap transaction from a quote.

        Returns the unsigned transaction to pass to the signer.
        """
        return await self._client.post(
            "/swap",
            json_data={
                "quoteResponse": quote_response,
                "userPublicKey": user_public_key,
                "wrapAndUnwrapSol": True,
                "computeUnitPriceMicroLamports": priority_fee_lamports,
                "dynamicComputeUnitLimit": True,
            },
        )

    async def get_quote_buy(
        self,
        token_mint: str,
        sol_amount_lamports: int,
        slippage_bps: int = 300,
    ) -> dict[str, Any]:
        """Convenience: get quote to buy a token with SOL."""
        return await self.get_quote(
            input_mint=SOL_MINT,
            output_mint=token_mint,
            amount=sol_amount_lamports,
            slippage_bps=slippage_bps,
        )

    async def get_quote_sell(
        self,
        token_mint: str,
        token_amount: int,
        slippage_bps: int = 300,
    ) -> dict[str, Any]:
        """Convenience: get quote to sell a token for SOL."""
        return await self.get_quote(
            input_mint=token_mint,
            output_mint=SOL_MINT,
            amount=token_amount,
            slippage_bps=slippage_bps,
        )

    async def close(self) -> None:
        await self._client.close()
