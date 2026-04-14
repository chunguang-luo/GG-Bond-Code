"""Minimal global state store — mirrors state/store.ts (35 lines)."""

from __future__ import annotations

from typing import Any


class _Store:
    """Simple key-value store. Singleton via module-level instance."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def has(self, key: str) -> bool:
        return key in self._data

    def snapshot(self) -> dict[str, Any]:
        return dict(self._data)


# Module-level singleton instance
_store_instance = _Store()


def get_store() -> _Store:
    """Get the global store instance."""
    return _store_instance


# For backward compatibility, allow Store() to return the singleton
def Store() -> _Store:
    """Return the global store singleton."""
    return _store_instance
