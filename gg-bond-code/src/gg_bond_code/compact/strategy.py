"""Compact strategy - compress conversation history when context is full."""

from abc import ABC, abstractmethod


class CompactStrategy(ABC):
    """Base class for compact strategies."""

    @abstractmethod
    async def should_compact(
        self,
        messages: list[dict],
        model: str,
        current_tokens: int,
        max_tokens: int,
    ) -> bool:
        """Check if compaction should be performed."""
        pass

    @abstractmethod
    async def compact(
        self,
        messages: list[dict],
        model: str,
    ) -> tuple[list[dict], str]:
        """Perform compaction and return (compressed_messages, reason)."""
        pass


class TokenCountStrategy(CompactStrategy):
    """Compact when token count exceeds limit."""

    def __init__(self, threshold_tokens: int = 8000):
        self.threshold_tokens = threshold_tokens

    async def should_compact(
        self,
        messages: list[dict],
        model: str,
        current_tokens: int,
        max_tokens: int,
    ) -> bool:
        """Check if compaction should be performed."""
        return current_tokens > self.threshold_tokens

    async def compact(self, messages: list[dict], model: str) -> tuple[list[dict], str]:
        """Perform simple truncation."""
        # Keep last N messages
        keep_count = max(1, len(messages) // 2)
        compacted = messages[-keep_count:]
        reason = f"Truncated to last {keep_count} messages due to token limit"
        return compacted, reason


class MessageCountStrategy(CompactStrategy):
    """Compact when message count exceeds limit."""

    def __init__(self, max_messages: int = 10):
        self.max_messages = max_messages

    async def should_compact(
        self,
        messages: list[dict],
        model: str,
        current_tokens: int,
        max_tokens: int,
    ) -> bool:
        """Check if compaction should be performed."""
        return len(messages) > self.max_messages

    async def compact(self, messages: list[dict], model: str) -> tuple[list[dict], str]:
        """Perform simple truncation."""
        compacted = messages[-self.max_messages:]
        reason = f"Limited to last {self.max_messages} messages"
        return compacted, reason


async def should_compact_messages(
    messages: list[dict],
    model: str,
    strategy: CompactStrategy | None = None,
) -> tuple[bool, CompactStrategy]:
    """Check if compaction should be performed.

    Args:
        messages: Current message list
        model: Model name
        strategy: Strategy to use, defaults to MessageCountStrategy

    Returns:
        Tuple of (should_compact, strategy_to_use)
    """
    if strategy is None:
        strategy = MessageCountStrategy()

    return await strategy.should_compact(messages, model, 0, 0), strategy
