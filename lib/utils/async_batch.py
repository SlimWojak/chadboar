"""Async batch utilities for parallel API operations."""
from __future__ import annotations

import asyncio
from typing import Any, Callable, TypeVar, Sequence


T = TypeVar('T')
R = TypeVar('R')


async def batch_gather(
    items: Sequence[T],
    async_fn: Callable[[T], Any],
    max_concurrent: int = 5,
    continue_on_error: bool = True,
) -> list[R | None]:
    """Execute async function on items with concurrency limit.
    
    Args:
        items: Items to process
        async_fn: Async function to call on each item
        max_concurrent: Max concurrent operations
        continue_on_error: If True, errors return None; if False, propagate
    
    Returns:
        List of results (None for failed items if continue_on_error=True)
    """
    semaphore = asyncio.Semaphore(max_concurrent)
    
    async def bounded_call(item: T) -> R | None:
        async with semaphore:
            try:
                return await async_fn(item)
            except Exception as e:
                if not continue_on_error:
                    raise
                # Log error silently and return None
                return None
    
    return await asyncio.gather(*[bounded_call(item) for item in items])


async def batch_price_fetch(
    birdeye_client: Any,
    mints: list[str],
    max_concurrent: int = 3,
) -> dict[str, dict[str, Any]]:
    """Fetch prices for multiple tokens in parallel.
    
    Args:
        birdeye_client: BirdeyeClient instance
        mints: List of token mint addresses
        max_concurrent: Max concurrent API calls
    
    Returns:
        Dict mapping mint -> price data (empty dict on failure)
    """
    async def fetch_one(mint: str) -> tuple[str, dict[str, Any]]:
        try:
            result = await birdeye_client.get_token_overview(mint)
            return (mint, result)
        except Exception:
            return (mint, {})
    
    results = await batch_gather(mints, fetch_one, max_concurrent=max_concurrent)
    return {mint: data for mint, data in results if data is not None}
