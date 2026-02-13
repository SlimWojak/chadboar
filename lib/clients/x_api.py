"""X (Twitter) API client — Narrative and sentiment detection.

Used by Narrative Hunter for KOL mentions, sentiment volume,
and social signal detection.
"""

from __future__ import annotations

import os
from typing import Any

from lib.clients.base import BaseClient
from lib.utils.retry import with_retry
from lib.utils.rate_limiter import get_rate_limiter


class XClient:
    """X API v2: search tweets, count mentions."""

    def __init__(self, bearer_token: str | None = None):
        self.bearer_token = bearer_token or os.environ.get("X_BEARER_TOKEN", "")
        self._client = BaseClient(
            base_url="https://api.twitter.com/2",
            headers={"Authorization": f"Bearer {self.bearer_token}"},
            rate_limit=1.0,  # Conservative: ~300 req/15 min
            timeout=10.0,
            provider_name="x_api",
        )

    @with_retry
    async def search_recent(
        self,
        query: str,
        max_results: int = 50,
    ) -> dict[str, Any]:
        """Search recent tweets (last 7 days)."""
        # Rate limit: min 1 second between X API calls
        limiter = get_rate_limiter()
        await limiter.wait_if_needed("x_api", min_interval_sec=1.0)
        
        return await self._client.get(
            "/tweets/search/recent",
            params={
                "query": query,
                "max_results": min(max_results, 100),
                "tweet.fields": "created_at,public_metrics,author_id",
                "expansions": "author_id",
                "user.fields": "public_metrics,verified",
            },
            cache_ttl=60,
        )

    @with_retry
    async def count_recent(self, query: str) -> dict[str, Any]:
        """Count tweets matching query in recent timeframes."""
        # Rate limit: min 1 second between X API calls
        limiter = get_rate_limiter()
        await limiter.wait_if_needed("x_api", min_interval_sec=1.0)
        
        return await self._client.get(
            "/tweets/counts/recent",
            params={"query": query, "granularity": "hour"},
            cache_ttl=60,
        )

    async def close(self) -> None:
        await self._client.close()
