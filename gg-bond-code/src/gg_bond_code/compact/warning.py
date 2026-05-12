"""Compact warning manager — proactive alerts about context window pressure.

Three roles:
1. Evaluate current token usage and determine warning level
2. Suppress warnings immediately after compact (token count is inaccurate)
3. Clear suppression after microcompact or new turn (restore real warnings)
"""

from __future__ import annotations

from dataclasses import dataclass

from .budget import (
    calculate_token_warning_state,
    get_effective_context_window,
)


@dataclass(frozen=True)
class WarningLevel:
    """Warning display information for the UI."""

    level: str  # "ok" | "warning" | "error" | "blocking"
    message: str  # Human-readable message
    percent_used: int  # Token usage percentage
    token_usage: int  # Raw token count
    effective_window: int  # Effective context window size


class CompactWarningManager:
    """Manage warning state with post-compact suppression.

    After compact, the token count drops sharply and is temporarily
    inaccurate. Suppressing warnings for one turn after compact
    prevents flickering/confusing warnings.
    """

    def __init__(self) -> None:
        self._suppressed: bool = False

    def evaluate(self, token_usage: int, model: str) -> WarningLevel:
        """Evaluate current warning level based on token usage.

        Args:
            token_usage: Estimated token count of current messages.
            model: Model name for context window lookup.

        Returns:
            WarningLevel with level, message, and usage details.
        """
        warning_state = calculate_token_warning_state(token_usage, model)
        effective = get_effective_context_window(model)
        percent_used = round((token_usage / effective) * 100) if effective > 0 else 0

        if warning_state.is_at_blocking:
            level = "blocking"
            message = "Context window full. Use /compact to compress."
        elif warning_state.is_above_auto_compact:
            level = "error"
            message = "Context nearly full. Auto-compact will trigger soon."
        elif warning_state.is_above_warning:
            level = "warning"
            message = "Context usage is getting high."
        else:
            level = "ok"
            message = ""

        return WarningLevel(
            level=level,
            message=message,
            percent_used=min(percent_used, 100),
            token_usage=token_usage,
            effective_window=effective,
        )

    def suppress(self) -> None:
        """Suppress warnings after Full Compact.

        Called after Full Compact because the token count drops
        sharply and is temporarily inaccurate.
        """
        self._suppressed = True

    def clear_suppression(self) -> None:
        """Clear warning suppression.

        Called after Microcompact or at the start of a new turn
        to restore real warning display.
        """
        self._suppressed = False

    @property
    def is_suppressed(self) -> bool:
        """Whether warnings are currently suppressed."""
        return self._suppressed
