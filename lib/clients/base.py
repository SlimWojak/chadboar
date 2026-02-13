"""Base HTTP client for ChadBoar API layer.

Provides:
- Rate limiting (per-endpoint token bucket)
- Automatic retry with exponential backoff
- Timeout handling
- Response caching (TTL-based)
- RPC fallback chain rotation
- Structured error handling

All API clients inherit from this base.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class RateLimiter:
    """Simple token-bucket rate limiter."""

    max_per_second: float
    _tokens: float = field(init=False)
    _last_refill: float = field(init=False)

    def __post_init__(self) -> None:
        self._tokens = self.max_per_second
        self._last_refill = time.monotonic()

    def acquire(self) -> float:
        """Acquire a token. Returns wait time in seconds (0 if immediate)."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.max_per_second, self._tokens + elapsed * self.max_per_second)
        self._last_refill = now

        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return 0.0

        wait = (1.0 - self._tokens) / self.max_per_second
        return wait


@dataclass
class CacheEntry:
    """TTL-based cache entry."""

    data: Any
    expires_at: float


class ResponseCache:
    """Simple in-memory TTL cache for API responses."""

    def __init__(self) -> None:
        self._store: dict[str, CacheEntry] = {}

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        if time.monotonic() > entry.expires_at:
            del self._store[key]
            return None
        return entry.data

    def set(self, key: str, data: Any, ttl_seconds: float) -> None:
        self._store[key] = CacheEntry(data=data, expires_at=time.monotonic() + ttl_seconds)

    def clear(self) -> None:
        self._store.clear()


class APIError(Exception):
    """Structured API error."""

    def __init__(self, message: str, status_code: int = 0, provider: str = "", retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.provider = provider
        self.retryable = retryable


class BaseClient:
    """Base HTTP client with retry, rate limiting, and caching.

    Usage:
        client = BaseClient(
            base_url="https://api.example.com",
            headers={"Authorization": "Bearer xxx"},
            rate_limit=5.0,  # 5 req/sec
            timeout=10.0,
        )
        data = await client.get("/endpoint", params={"q": "test"}, cache_ttl=60)
    """

    def __init__(
        self,
        base_url: str = "",
        headers: dict[str, str] | None = None,
        rate_limit: float = 10.0,
        timeout: float = 10.0,
        max_retries: int = 3,
        backoff_base: float = 1.0,
        backoff_max: float = 60.0,
        backoff_multiplier: float = 2.0,
        provider_name: str = "",
    ):
        self.base_url = base_url.rstrip("/")
        self.provider_name = provider_name
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.backoff_multiplier = backoff_multiplier
        self._rate_limiter = RateLimiter(max_per_second=rate_limit)
        self._cache = ResponseCache()
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers or {},
            timeout=httpx.Timeout(timeout),
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        cache_ttl: float = 0,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """GET request with rate limiting, retry, and optional caching."""
        cache_key = f"GET:{path}:{params}" if cache_ttl > 0 else ""
        if cache_key:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

        data = await self._request("GET", path, params=params, headers=headers)

        if cache_key and cache_ttl > 0:
            self._cache.set(cache_key, data, cache_ttl)

        return data

    async def post(
        self,
        path: str,
        json_data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """POST request with rate limiting and retry."""
        return await self._request("POST", path, json_data=json_data, headers=headers)

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Execute request with retry and backoff."""
        last_error: Exception | None = None
        delay = self.backoff_base

        for attempt in range(self.max_retries + 1):
            # Rate limit
            wait = self._rate_limiter.acquire()
            if wait > 0:
                await asyncio.sleep(wait)

            try:
                response = await self._client.request(
                    method,
                    path,
                    params=params,
                    json=json_data,
                    headers=headers,
                )

                if response.status_code == 429:
                    retry_after = float(response.headers.get("retry-after", delay))
                    raise APIError(
                        f"Rate limited by {self.provider_name}",
                        status_code=429,
                        provider=self.provider_name,
                        retryable=True,
                    )

                if response.status_code >= 500:
                    raise APIError(
                        f"Server error from {self.provider_name}: {response.status_code}",
                        status_code=response.status_code,
                        provider=self.provider_name,
                        retryable=True,
                    )

                if response.status_code >= 400:
                    raise APIError(
                        f"Client error from {self.provider_name}: {response.status_code} — {response.text[:200]}",
                        status_code=response.status_code,
                        provider=self.provider_name,
                        retryable=False,
                    )

                return response.json()

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                last_error = APIError(
                    f"Connection error to {self.provider_name}: {e}",
                    provider=self.provider_name,
                    retryable=True,
                )
            except APIError as e:
                last_error = e
                if not e.retryable:
                    raise

            # Backoff before retry
            if attempt < self.max_retries:
                await asyncio.sleep(min(delay, self.backoff_max))
                delay *= self.backoff_multiplier

        raise last_error or APIError(f"Request failed after {self.max_retries} retries")


class RPCFallbackClient:
    """RPC client with automatic fallback chain rotation.

    Tries primary RPC first, falls back to secondary/tertiary on failure.
    Exponential backoff on each provider before moving to the next.
    """

    def __init__(self, endpoints: list[dict[str, Any]]):
        self._endpoints = endpoints
        self._clients: list[BaseClient] = []
        for ep in endpoints:
            self._clients.append(
                BaseClient(
                    base_url=ep["url"],
                    rate_limit=ep.get("rate_limit", 10.0),
                    timeout=ep.get("timeout_seconds", 10.0),
                    provider_name=ep.get("provider", "unknown"),
                    max_retries=1,  # Quick fail per-provider, fallback handles retry
                )
            )

    async def request(
        self,
        method: str,
        path: str = "",
        json_data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Try each RPC endpoint in order. Return first success."""
        errors: list[str] = []
        for i, client in enumerate(self._clients):
            try:
                if method == "POST":
                    return await client.post(path, json_data=json_data)
                else:
                    return await client.get(path, params=params)
            except APIError as e:
                errors.append(f"{client.provider_name}: {e}")
                continue

        raise APIError(
            f"All RPC endpoints failed: {'; '.join(errors)}",
            provider="rpc_fallback",
            retryable=False,
        )

    async def close(self) -> None:
        for client in self._clients:
            await client.close()
