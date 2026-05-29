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
    3. Ensures Anthropic-format tool_result messages immediately follow
       the assistant message containing the corresponding tool_use.
    """
    tool_ids = _collect_tool_ids(messages)
    result_ids = _collect_result_ids(messages)

    orphan_results = result_ids - tool_ids
    orphan_uses = tool_ids - result_ids

    if not orphan_results and not orphan_uses:
        # Even with matching IDs, check Anthropic ordering constraint:
        # tool_result must immediately follow the assistant message with tool_use.
        messages = _repair_anthropic_ordering(messages)
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

    # After removing orphans, also fix ordering
    repaired = _repair_anthropic_ordering(repaired)
    return repaired


def _repair_anthropic_ordering(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ensure Anthropic-format tool_result messages immediately follow tool_use.

    Anthropic's API requires that each tool_use in an assistant message
    has its corresponding tool_result in the very next user message.
    If there's an intervening message (e.g. another assistant message),
    the tool_result must be moved to immediately follow its tool_use.

    Also handles the case where tool_result exists but is separated from
    its tool_use by other messages.
    """
    if not messages:
        return messages

    # Check if this looks like Anthropic format at all
    has_anthropic_tool_use = False
    for msg in messages:
        if msg.get("role") == "assistant":
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        has_anthropic_tool_use = True
                        break
        if has_anthropic_tool_use:
            break

    if not has_anthropic_tool_use:
        return messages

    # Build a map: assistant_msg_index -> set of tool_use_ids in that message
    # Also track: tool_use_id -> which assistant_msg_index
    tool_use_locations: dict[str, int] = {}  # tool_use_id -> index of assistant msg
    for i, msg in enumerate(messages):
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tid = block.get("id", "")
                if tid:
                    tool_use_locations[tid] = i

    if not tool_use_locations:
        return messages

    # Check if any tool_result is NOT immediately after its tool_use's assistant msg.
    # In Anthropic format, the tool_result must be in the user message that
    # directly follows the assistant message containing the tool_use.
    needs_repair = False
    for i, msg in enumerate(messages):
        role = msg.get("role")
        if role != "user":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                tid = block.get("tool_use_id", "")
                if tid in tool_use_locations:
                    expected_pos = tool_use_locations[tid] + 1
                    if i != expected_pos:
                        needs_repair = True
                        break
        if needs_repair:
            break

    if not needs_repair:
        return messages

    # Rebuild messages with correct ordering:
    # For each assistant message with tool_use, ensure the very next message
    # is a user message containing all corresponding tool_results.
    result: list[dict[str, Any]] = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        result.append(msg)

        # If this is an assistant message with tool_use, find and insert
        # the corresponding tool_results right after it
        if msg.get("role") == "assistant":
            content = msg.get("content")
            if isinstance(content, list):
                tool_use_ids_in_msg: list[str] = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tid = block.get("id", "")
                        if tid:
                            tool_use_ids_in_msg.append(tid)

                if tool_use_ids_in_msg:
                    # Collect tool_result blocks for these IDs from anywhere in messages
                    tool_result_blocks: list[dict[str, Any]] = []
                    remaining_msg_indices: set[int] = set(range(len(messages)))

                    for j, other_msg in enumerate(messages):
                        if other_msg.get("role") != "user":
                            continue
                        other_content = other_msg.get("content")
                        if not isinstance(other_content, list):
                            continue
                        for block in other_content:
                            if isinstance(block, dict) and block.get("type") == "tool_result":
                                tid = block.get("tool_use_id", "")
                                if tid in tool_use_ids_in_msg:
                                    tool_result_blocks.append(block)

                    # Find if the next message already has these tool_results
                    # and is in the correct position
                    next_idx = i + 1
                    if next_idx < len(messages) and messages[next_idx].get("role") == "user":
                        next_content = messages[next_idx].get("content")
                        if isinstance(next_content, list):
                            next_result_ids = {
                                b.get("tool_use_id", "")
                                for b in next_content
                                if isinstance(b, dict) and b.get("type") == "tool_result"
                            }
                            if next_result_ids == set(tool_use_ids_in_msg):
                                # Correct ordering already, skip to next
                                i += 1
                                continue

                    if tool_result_blocks:
                        # Insert a user message with tool_results
                        result.append({
                            "role": "user",
                            "content": tool_result_blocks,
                        })

        i += 1

    # Remove duplicate tool_result user messages that were in wrong positions
    # Second pass: remove user messages that only contained tool_results
    # which are now correctly placed elsewhere
    seen_tool_result_ids: set[str] = set()
    final: list[dict[str, Any]] = []

    for msg in result:
        role = msg.get("role")
        if role == "user":
            content = msg.get("content")
            if isinstance(content, list):
                # Check if this is a tool_result-only message
                tool_result_blocks = [
                    b for b in content
                    if isinstance(b, dict) and b.get("type") == "tool_result"
                ]
                non_tool_blocks = [
                    b for b in content
                    if not (isinstance(b, dict) and b.get("type") == "tool_result")
                ]

                if tool_result_blocks:
                    # Filter out already-seen tool_results
                    new_blocks = []
                    for b in tool_result_blocks:
                        tid = b.get("tool_use_id", "")
                        if tid not in seen_tool_result_ids:
                            seen_tool_result_ids.add(tid)
                            new_blocks.append(b)

                    if not new_blocks and not non_tool_blocks:
                        # All tool_results were duplicates and no other content
                        continue
                    elif new_blocks and not non_tool_blocks:
                        msg = {**msg, "content": new_blocks}
                    elif new_blocks:
                        msg = {**msg, "content": non_tool_blocks + new_blocks}
                    # If no new_blocks but has non_tool_blocks, keep non-tool content
                    elif non_tool_blocks:
                        msg = {**msg, "content": non_tool_blocks}

        final.append(msg)

    return final


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
