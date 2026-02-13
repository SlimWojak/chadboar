"""Retry and backoff utilities for external API calls."""
from __future__ import annotations

from functools import wraps
from typing import Any, Callable, TypeVar

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)
import aiohttp


# Type variable for decorated functions
F = TypeVar('F', bound=Callable[..., Any])


def with_retry(func: F) -> F:
    """Decorator for async functions that call external APIs.
    
    Retries on network errors with exponential backoff.
    - 3 attempts max
    - 1s initial wait, 10s max wait
    - Only retries on aiohttp errors (not on 4xx HTTP codes)
    """
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((
            aiohttp.ClientError,
            aiohttp.ClientConnectionError,
            ConnectionError,
            TimeoutError,
        )),
        reraise=True,
    )
    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        return await func(*args, **kwargs)
    
    return wrapper  # type: ignore
