"""Smart rate limiter for API calls with per-provider tracking."""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import Any, Callable, TypeVar


F = TypeVar('F', bound=Callable[..., Any])


class RateLimiter:
    """Per-provider rate limiter with sliding window tracking.
    
    Prevents hitting API rate limits by tracking call timestamps
    and enforcing minimum delay between calls.
    """
    
    def __init__(self):
        # provider_name -> list of timestamps
        self._call_history: dict[str, list[float]] = defaultdict(list)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
    
    async def wait_if_needed(self, provider: str, min_interval_sec: float) -> None:
        """Wait if needed to respect minimum interval between calls.
        
        Args:
            provider: Provider name (e.g., 'x_api', 'birdeye')
            min_interval_sec: Minimum seconds between calls
        """
        async with self._locks[provider]:
            now = time.time()
            history = self._call_history[provider]
            
            # Clean old entries (keep last 1 hour)
            cutoff = now - 3600
            self._call_history[provider] = [t for t in history if t > cutoff]
            
            # Check if we need to wait
            if self._call_history[provider]:
                last_call = self._call_history[provider][-1]
                time_since = now - last_call
                
                if time_since < min_interval_sec:
                    wait_time = min_interval_sec - time_since
                    await asyncio.sleep(wait_time)
                    now = time.time()
            
            # Record this call
            self._call_history[provider].append(now)
    
    def get_call_count(self, provider: str, window_sec: float = 60.0) -> int:
        """Get number of calls made in the last N seconds."""
        now = time.time()
        cutoff = now - window_sec
        history = self._call_history.get(provider, [])
        return len([t for t in history if t > cutoff])


# Global rate limiter instance
_rate_limiter = RateLimiter()


def get_rate_limiter() -> RateLimiter:
    """Get the global rate limiter instance."""
    return _rate_limiter
