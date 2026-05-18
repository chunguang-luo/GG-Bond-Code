"""Prefetch strategies - compute expensive context during idle time."""

from __future__ import annotations

import asyncio
from typing import Any

_prefetch_tasks: set[asyncio.Task[Any]] = set()


async def start_deferred_prefetches() -> None:
    """Start background prefetch tasks after REPL starts.

    Prefetch strategy: compute context during idle time to improve response speed.
    """
    # Prefetch user context (date, NEXTCODE.md)
    await _prefetch_user_context()

    # Prefetch system context (Git status, etc.)
    await _prefetch_system_context()


async def _prefetch_user_context() -> None:
    """Prefetch user context (date, NEXTCODE.md) in background."""
    from .context.user import get_user_context
    from .state.store import Store

    # Get CWD from Store
    store = Store()
    cwd = store.get("cwd")

    # Prefetch user context to warm up the cache
    try:
        get_user_context(cwd)
    except Exception:
        # Prefetch failure should not block REPL startup
        pass


async def _prefetch_system_context() -> None:
    """Prefetch system context (Git status, etc.) in background."""
    from .context.system import get_system_context
    from .state.store import Store

    # Get CWD from Store
    store = Store()
    cwd = store.get("cwd")

    # Prefetch system context to warm up the cache
    try:
        get_system_context(cwd)
    except Exception:
        # Prefetch failure should not block REPL startup
        pass


def get_prefetch_status() -> dict[str, Any]:
    """Get current prefetch status for debugging.

    Returns:
        Dictionary with prefetch task information
    """
    return {
        "user_context": "started",
        "system_context": "started",
        "total_tasks": len(_prefetch_tasks),
    }
