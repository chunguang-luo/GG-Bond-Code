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
    MAX_FILES_POST_COMPACT,
    MAX_TOKENS_PER_FILE,
    POST_COMPACT_TOKEN_BUDGET,
    TokenWarningState,
    calculate_token_warning_state,
    estimate_token_count,
    get_auto_compact_threshold,
)
from .circuit_breaker import CompactCircuitBreaker
from .file_cache import FileStateCache
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

    def __init__(self, model: str, file_cache: FileStateCache | None = None) -> None:
        self._model = model
        self._circuit_breaker = CompactCircuitBreaker(max_failures=3)
        self._full_strategy = FullCompactStrategy()
        self._file_cache = file_cache
        self._session_memory_manager: Any = None  # SessionMemoryManager, set externally

    @property
    def circuit_breaker(self) -> CompactCircuitBreaker:
        return self._circuit_breaker

    def set_session_memory_manager(self, manager: Any) -> None:
        """Set the SessionMemoryManager for compact coordination."""
        self._session_memory_manager = manager

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
            # Try to use Session Memory before compacting
            if self._session_memory_manager is not None:
                session_content = await self._session_memory_manager.wait_for_extraction_async()
                if session_content:
                    # Use Session Memory as compact summary (saves an API call)
                    compacted = self._build_session_memory_compact(messages, session_content)
                    # Re-inject recently accessed file contents after compact
                    compacted = self._rebuild_after_compact(compacted)
                    if self._file_cache is not None:
                        self._file_cache.clear()
                    return compacted, "Compacted using Session Memory (no API call needed)"

            # Fall back to model-summarized compact
            compacted, reason = await self._full_strategy.compact(messages, self._model)
            # Sync circuit breaker state
            self._circuit_breaker._consecutive_failures = (
                self._full_strategy.circuit_breaker.consecutive_failures
            )
            # Re-inject recently accessed file contents after compact
            compacted = self._rebuild_after_compact(compacted)
            # Clear file cache (contents are now in the message history)
            if self._file_cache is not None:
                self._file_cache.clear()
            return compacted, reason

        return messages, "Unknown compact level"

    def get_token_usage(self, messages: list[dict[str, Any]]) -> int:
        """Estimate token usage for a message list."""
        return estimate_token_count(messages)

    def _build_session_memory_compact(
        self,
        messages: list[dict[str, Any]],
        session_content: str,
    ) -> list[dict[str, Any]]:
        """Build compacted messages using Session Memory content.

        Replaces the full conversation with a summary message containing
        the Session Memory, plus the most recent user message.
        """
        summary = f"[Context compacted — Session Memory]\n\n{session_content}"
        summary_message = {"role": "user", "content": summary}

        if not messages:
            return [summary_message]

        # Keep the most recent user message if available
        recent_user = None
        for msg in reversed(messages):
            if msg.get("role") == "user":
                recent_user = msg
                break

        if recent_user:
            return [summary_message, recent_user]

        return [summary_message]

    def _rebuild_after_compact(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Re-inject recently accessed file contents after Full Compact.

        After compact, the model loses knowledge of recently accessed files.
        This method injects their contents as a user message so the model
        retains context about files it was working on, within token budget.
        """
        if self._file_cache is None:
            return messages

        recent = self._file_cache.get_recent(MAX_FILES_POST_COMPACT)
        if not recent:
            return messages

        # Priority sort: recently edited > recently read (then by recency)
        recent.sort(key=lambda e: (0 if e.was_edited else 1, -e.timestamp))

        # Build file content blocks with per-file token truncation
        file_blocks: list[str] = []
        total_tokens = 0

        for entry in recent:
            content = entry.content
            estimated_tokens = len(content) // 4

            if estimated_tokens > MAX_TOKENS_PER_FILE:
                # Truncate to per-file budget
                truncation_char = MAX_TOKENS_PER_FILE * 4
                content = content[:truncation_char] + "\n... (truncated)"

            entry_tokens = len(content) // 4
            if total_tokens + entry_tokens > POST_COMPACT_TOKEN_BUDGET:
                break

            label = f"--- {entry.path} (recently {'edited' if entry.was_edited else 'read'}) ---\n"
            file_blocks.append(label + content)
            total_tokens += entry_tokens

        if not file_blocks:
            return messages

        # Inject as a user message after the summary (index 0)
        attachment = (
            "[Recently accessed files after context compaction]\n\n"
            + "\n\n".join(file_blocks)
        )
        inject_message = {"role": "user", "content": attachment}

        # Insert after the summary message but before recent messages
        return [messages[0], inject_message] + messages[1:]
