"""Minimal global state store — mirrors state/store.ts (35 lines).

Adds subscribe/onChange support and deep-copy snapshot,
aligning with Claude Code's Store pattern.
"""

from __future__ import annotations

import copy
from typing import Any, Callable

# Sentinel for detecting missing keys (distinguishes None values from absent keys)
_SENTINEL = object()

# Type aliases
Listener = Callable[[str, Any, Any], None]  # (key, new_value, old_value)
OnChange = Callable[[str, Any, Any], None]  # same signature as Listener


class _Store:
    """Simple key-value store with subscribe and onChange support.

    Singleton via module-level instance. Mirrors Claude Code's store.ts API:
    - getState  → get
    - setState  → set (with equality check + notification)
    - subscribe → subscribe (returns unsubscribe function)
    - onChange  → constructor parameter for global side-effects
    """

    def __init__(self, on_change: OnChange | None = None) -> None:
        self._data: dict[str, Any] = {}
        self._listeners: set[Listener] = set()
        self._on_change = on_change

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a value and notify listeners if it changed.

        Uses equality check (==) to skip notifications when the value
        hasn't actually changed, avoiding unnecessary work — mirrors
        Claude Code's Object.is check.
        """
        old = self._data.get(key, _SENTINEL)
        # Skip notification if value is unchanged
        if old is not _SENTINEL and old == value:
            return
        self._data[key] = value
        # Fire onChange callback first (mirrors Claude Code's ordering)
        if self._on_change is not None:
            self._on_change(key, value, old if old is not _SENTINEL else None)
        # Then notify all subscribers
        for listener in self._listeners:
            listener(key, value, old if old is not _SENTINEL else None)

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def has(self, key: str) -> bool:
        return key in self._data

    def snapshot(self) -> dict[str, Any]:
        """Return a deep copy of the current state.

        Deep copy prevents external code from mutating store internals
        through retained references — mirrors Claude Code's DeepImmutable
        guarantee at runtime.
        """
        return copy.deepcopy(self._data)

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        """Subscribe to state changes. Returns an unsubscribe function.

        Mirrors Claude Code's subscribe API:
          const unsub = store.subscribe(listener)
        """
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)


# Module-level singleton instance
_store_instance = _Store()


def get_store() -> _Store:
    """Get the global store instance."""
    return _store_instance


def reset_store(on_change: OnChange | None = None) -> _Store:
    """Reset the global store (for testing or session switching).

    Creates a fresh singleton, optionally with a new onChange callback.
    Returns the new instance so callers can immediately use it.
    """
    global _store_instance
    _store_instance = _Store(on_change=on_change)
    return _store_instance


# For backward compatibility, allow Store() to return the singleton
def Store() -> _Store:
    """Return the global store singleton."""
    return _store_instance
