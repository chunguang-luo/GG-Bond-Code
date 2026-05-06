"""Compact strategy - compress conversation history when context is full."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


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


def _collect_tool_ids(messages: list[dict[str, Any]]) -> set[str]:
    """Collect all tool_use IDs from assistant messages."""
    ids: set[str] = set()
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        # Anthropic: content is a list of blocks
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tid = block.get("id")
                    if tid:
                        ids.add(tid)
        # OpenAI: tool_calls list
        for tc in msg.get("tool_calls", []):
            tid = tc.get("id")
            if tid:
                ids.add(tid)
    return ids


def _collect_result_ids(messages: list[dict[str, Any]]) -> set[str]:
    """Collect all tool_result IDs from user/tool messages."""
    ids: set[str] = set()
    for msg in messages:
        role = msg.get("role")
        if role == "user":
            # Anthropic: content is a list of blocks
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        tid = block.get("tool_use_id")
                        if tid:
                            ids.add(tid)
        elif role == "tool":
            # OpenAI: tool_call_id field
            tid = msg.get("tool_call_id")
            if tid:
                ids.add(tid)
    return ids


def repair_tool_references(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Repair truncated tool references after compaction.

    After simple truncation, assistant messages may contain tool_use blocks
    whose tool_result was cut off, or tool_result messages whose tool_use
    was cut off. Both cases cause API 400 errors.

    This function:
    1. Finds orphaned tool_results (no matching tool_use) and removes them
    2. Finds orphaned tool_uses (no matching tool_result) and removes them
       from the assistant message content, removing the message entirely
       if it becomes empty.
    """
    tool_ids = _collect_tool_ids(messages)
    result_ids = _collect_result_ids(messages)

    orphan_results = result_ids - tool_ids
    orphan_uses = tool_ids - result_ids

    if not orphan_results and not orphan_uses:
        return messages

    repaired: list[dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role")

        # Remove orphaned tool_result blocks from user messages (Anthropic)
        if role == "user" and orphan_results:
            content = msg.get("content")
            if isinstance(content, list):
                filtered = [
                    b for b in content
                    if not (isinstance(b, dict) and b.get("type") == "tool_result"
                            and b.get("tool_use_id") in orphan_results)
                ]
                if not filtered:
                    # Skip this message entirely if all blocks were orphaned
                    continue
                if len(filtered) < len(content):
                    msg = {**msg, "content": filtered}

        # Remove orphaned tool messages (OpenAI)
        if role == "tool" and msg.get("tool_call_id") in orphan_results:
            continue

        # Remove orphaned tool_use blocks from assistant messages
        if role == "assistant" and orphan_uses:
            content = msg.get("content")
            if isinstance(content, list):
                filtered = [
                    b for b in content
                    if not (isinstance(b, dict) and b.get("type") == "tool_use"
                            and b.get("id") in orphan_uses)
                ]
                if not filtered:
                    # Skip assistant message if it only had orphaned tool_uses
                    continue
                if len(filtered) < len(content):
                    msg = {**msg, "content": filtered}

            # OpenAI: remove orphaned tool_calls
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                filtered_tc = [
                    tc for tc in tool_calls
                    if tc.get("id") not in orphan_uses
                ]
                if not filtered_tc:
                    # Only had orphaned tool_calls — keep text content only
                    msg = {k: v for k, v in msg.items() if k != "tool_calls"}
                    if not msg.get("content"):
                        continue
                elif len(filtered_tc) < len(tool_calls):
                    msg = {**msg, "tool_calls": filtered_tc}

        repaired.append(msg)

    return repaired


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
        """Perform simple truncation with tool reference repair."""
        keep_count = max(1, len(messages) // 2)
        compacted = messages[-keep_count:]
        compacted = repair_tool_references(compacted)
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
        """Perform simple truncation with tool reference repair."""
        compacted = messages[-self.max_messages:]
        compacted = repair_tool_references(compacted)
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
