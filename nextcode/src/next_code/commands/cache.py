"""Command system caching — memoize + clear caches.

Provides:
- ``memoize_async``: decorator that caches async function results in-memory
  until explicitly cleared.
- ``clear_command_caches``: clears all memoized caches at once.

Unlike the plan's Store-backed approach, this uses a simple in-memory dict
per decorated function. This avoids coupling caching to the global Store
and avoids serialisation issues with complex return types.
"""

from __future__ import annotations

import asyncio
from functools import wraps
from typing import Any, Callable


# Registry of all memoize caches for bulk clearing
_memo_caches: dict[str, dict[tuple[Any, ...], Any]] = {}


def memoize_async(fn: Callable) -> Callable:
    """Async memoize — caches result in-memory until explicitly cleared.

    Cache key is built from positional args (keyword args are ignored).
    This is intentionally simple — command loading is cheap enough that
    cache hits are a nice-to-have, not a correctness requirement.
    """
    cache: dict[tuple[Any, ...], Any] = {}
    cache_name = f"__memo_{fn.__qualname__}"
    _memo_caches[cache_name] = cache

    @wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        key = args
        if key in cache:
            return cache[key]
        result = await fn(*args, **kwargs)
        cache[key] = result
        return result

    def cache_clear() -> None:
        cache.clear()

    wrapper.cache_clear = cache_clear  # type: ignore[attr-defined]
    wrapper._cache_name = cache_name  # type: ignore[attr-defined]
    return wrapper


def clear_command_caches() -> None:
    """Clear all command-related memoized caches.

    Call this when the command set changes (e.g. after skill loading,
    context switch, or configuration change).
    """
    for cache in _memo_caches.values():
        cache.clear()
