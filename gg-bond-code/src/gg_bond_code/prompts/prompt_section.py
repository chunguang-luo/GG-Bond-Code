"""Prompt section management for system prompts.

This module provides a simple interface for system prompt sections.
All sections are static strings and computed on demand.

Note: Previously this module provided caching, but it was removed since
all sections are static strings with negligible computation cost.
"""

from __future__ import annotations

from typing import Any, Callable

# Boundary marker separating static and dynamic sections
# Static sections come before this marker, dynamic sections after
SYSTEM_PROMPT_DYNAMIC_BOUNDARY = "__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__"


class SystemPromptSection:
    """A section that returns static prompt content."""

    def __init__(
        self,
        name: str,
        compute: Callable[..., str | None],
    ) -> None:
        self.name = name
        self.compute = compute

    def resolve(self, *args: Any, **kwargs: Any) -> str | None:
        """Resolve the section by calling the compute function.

        Args:
            *args, **kwargs: Arguments to pass to compute function
        """
        return self.compute(*args, **kwargs)


def system_prompt_section(
    name: str,
    compute: Callable[..., str | None],
) -> SystemPromptSection:
    """Create a section that returns prompt content.

    Args:
        name: Section name for identification
        compute: Function that returns section content or None

    Returns:
        A SystemPromptSection object
    """
    return SystemPromptSection(name, compute)


def clear_section_cache() -> None:
    """No-op kept for API compatibility."""
    pass
