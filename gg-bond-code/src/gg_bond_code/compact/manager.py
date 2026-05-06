"""Compact Manager — orchestrates multi-level compaction.

Pressure gradient response:
1. Token usage below warning threshold → NONE
2. Token usage approaching auto-compact → MICRO (clear old tool results)
3. Token usage at auto-compact threshold → FULL (model-summarized compact)
4. Token usage at blocking limit → BLOCKING (refuse new queries)

Graceful degradation:
- FULL with open circuit breaker → degrades to MICRO
- MICRO with nothing to clear → escalates to FULL
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from .budget import (
    TokenWarningState,
    calculate_token_warning_state,
    estimate_token_count,
    get_auto_compact_threshold,
)
from .circuit_breaker import CompactCircuitBreaker
from .full import FullCompactStrategy
from .micro import microcompact_messages


class CompactLevel(Enum):
    """Compaction level, from lightest to heaviest."""

    NONE = "none"  # No compaction needed
    MICRO = "micro"  # Clear old tool results
    FULL = "full"  # Model-summarized compact
    BLOCKING = "blocking"  # Context full — block new queries


class CompactManager:
    """Orchestrate multi-level compaction based on token usage."""

    def __init__(self, model: str) -> None:
        self._model = model
        self._circuit_breaker = CompactCircuitBreaker(max_failures=3)
        self._full_strategy = FullCompactStrategy()

    @property
    def circuit_breaker(self) -> CompactCircuitBreaker:
        return self._circuit_breaker

    def evaluate(self, token_usage: int) -> tuple[CompactLevel, TokenWarningState]:
        """Evaluate what compaction level is needed.

        Args:
            token_usage: Estimated token count of current messages.

        Returns:
            Tuple of (compact_level, warning_state).
        """
        warning_state = calculate_token_warning_state(token_usage, self._model)

        if warning_state.is_at_blocking:
            return CompactLevel.BLOCKING, warning_state

        if warning_state.is_above_auto_compact:
            # Check circuit breaker — if open, fall back to micro
            if (
                self._circuit_breaker.is_open
                or self._full_strategy.circuit_breaker.is_open
            ):
                return CompactLevel.MICRO, warning_state
            return CompactLevel.FULL, warning_state

        if warning_state.is_above_warning:
            return CompactLevel.MICRO, warning_state

        return CompactLevel.NONE, warning_state

    async def execute(
        self,
        level: CompactLevel,
        messages: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], str]:
        """Execute the specified compaction level.

        Args:
            level: Compaction level to execute.
            messages: Current message list.

        Returns:
            Tuple of (compacted_messages, reason_string).
        """
        if level == CompactLevel.NONE:
            return messages, "No compaction needed"

        if level == CompactLevel.BLOCKING:
            return messages, "Context window full — use /compact to manually compress"

        if level == CompactLevel.MICRO:
            result = microcompact_messages(messages)
            if result.cleared_count > 0:
                return result.messages, (
                    f"Microcompact: cleared {result.cleared_count} tool results, "
                    f"freed ~{result.estimated_tokens_freed} tokens"
                )
            # Microcompact had nothing to clear — escalate to FULL
            level = CompactLevel.FULL

        if level == CompactLevel.FULL:
            compacted, reason = await self._full_strategy.compact(messages, self._model)
            # Sync circuit breaker state
            self._circuit_breaker._consecutive_failures = (
                self._full_strategy.circuit_breaker.consecutive_failures
            )
            return compacted, reason

        return messages, "Unknown compact level"

    def get_token_usage(self, messages: list[dict[str, Any]]) -> int:
        """Estimate token usage for a message list."""
        return estimate_token_count(messages)
