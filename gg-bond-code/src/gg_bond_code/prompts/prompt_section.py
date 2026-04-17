"""Prompt section management for system prompts.

This module provides caching and management for system prompt sections.
Sections can be static (cached) or dynamic (uncached).
"""

from __future__ import annotations

from typing import Any, Callable
import threading

# Boundary marker separating static (cacheable) and dynamic sections
SYSTEM_PROMPT_DYNAMIC_BOUNDARY = "__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__"

# Thread-safe cache storage
_section_cache: dict[str, str | None] = {}
_cache_lock = threading.Lock()


class SystemPromptSection:
    """A section that can be computed once and cached for session.

    Supports compute functions with parameters for dynamic sections.
    """

    def __init__(
        self,
        name: str,
        compute: Callable[..., str | None],
        cache_break: bool = False,
    ) -> None:
        self.name = name
        self.compute = compute
        self.cache_break = cache_break

    def resolve(self, *args: Any, **kwargs: Any) -> str | None:
        """Resolve the section, using cache if available.

        Args:
            *args, **kwargs: Arguments to pass to compute function
                (for dynamic sections like project_context that need cwd)
        """
        if self.cache_break:
            # Uncached section: always recompute
            result = self.compute(*args, **kwargs)
            return result

        # Cached section: check cache first
        with _cache_lock:
            if self.name in _section_cache:
                return _section_cache[self.name]

        # Not cached, compute and store
        result = self.compute(*args, **kwargs)
        with _cache_lock:
            _section_cache[self.name] = result
        return result


def system_prompt_section(
    name: str,
    compute: Callable[..., str | None],
) -> SystemPromptSection:
    """Create a section that is computed once and cached until /clear or /compact.

    Args:
        name: Section name for cache identification
        compute: Function that returns section content or None

    Returns:
        A SystemPromptSection object that caches the computed result
    """
    return SystemPromptSection(name, compute, cache_break=False)


def DANGEROUS_uncached_system_prompt_section(
    name: str,
    compute: Callable[..., str | None],
    reason: str,  # Must provide reason!
) -> SystemPromptSection:
    """Create a section that is recomputed every turn (breaks prompt cache).

    Use only when absolutely necessary - this increases API costs.

    Args:
        name: Section name for cache identification
        compute: Function that returns section content or None
        reason: Why this section must be uncached (for documentation)

    Returns:
        A SystemPromptSection object that always recomputes
    """
    return SystemPromptSection(name, compute, cache_break=True)


def clear_section_cache() -> None:
    """Clear all cached sections (called by /clear and /compact commands)."""
    with _cache_lock:
        _section_cache.clear()


def get_section_cache_stats() -> dict[str, str | None]:
    """Get current cache state (for debugging).

    Returns:
        A copy of the current cache dictionary
    """
    with _cache_lock:
        return _section_cache.copy()
