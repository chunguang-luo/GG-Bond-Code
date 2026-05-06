"""Microcompact — lightweight tool result cleanup without model calls.

Microcompact does not call the model to summarize. Instead, it directly
clears old tool results that have declining information value, replacing
them with a short placeholder message.

This is the lightest level of compaction — almost zero cost, minimal
information loss.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# Tool names whose results are safe to compact (high volume, declining value)
COMPACTABLE_TOOLS: frozenset[str] = frozenset({
    "Read",  # file_read tool
    "Glob",  # glob tool
    "Grep",  # grep tool
    "Bash",  # shell tool
    "Edit",  # file_edit tool (operation feedback)
    "Write",  # file_write tool (operation feedback)
})

TIME_BASED_MC_CLEARED_MESSAGE = "[Old tool result content cleared]"

# Default: keep the 3 most recent tool results
DEFAULT_KEEP_RECENT = 3


@dataclass
class MicrocompactResult:
    """Result of a microcompact operation."""

    messages: list[dict[str, Any]]
    cleared_count: int = 0
    estimated_tokens_freed: int = 0


def microcompact_messages(
    messages: list[dict[str, Any]],
    keep_recent: int = DEFAULT_KEEP_RECENT,
) -> MicrocompactResult:
    """Clear old compactable tool results, keeping the most recent N.

    Args:
        messages: Current message list.
        keep_recent: Number of recent tool results to preserve.

    Returns:
        MicrocompactResult with modified messages and cleanup stats.
    """
    # Step 1: Collect all compactable tool result positions
    compactable_positions = _collect_compactable_tool_results(messages)

    if not compactable_positions:
        return MicrocompactResult(messages=messages)

    # Step 2: Determine which to clear (most recent N are kept)
    sorted_positions = sorted(compactable_positions, key=lambda x: (x[0], x[1]))
    to_clear = (
        sorted_positions[:-keep_recent] if len(sorted_positions) > keep_recent else []
    )

    if not to_clear:
        return MicrocompactResult(messages=messages)

    # Step 3: Build new messages with cleared content
    clear_by_msg: dict[int, list[tuple[int, str]]] = {}
    for msg_idx, block_idx, tool_use_id in to_clear:
        clear_by_msg.setdefault(msg_idx, []).append((block_idx, tool_use_id))

    new_messages: list[dict[str, Any]] = []
    cleared_count = 0
    estimated_chars_freed = 0

    for i, msg in enumerate(messages):
        if i not in clear_by_msg:
            new_messages.append(msg)
            continue

        clear_set = {block_idx for block_idx, _ in clear_by_msg[i]}

        if msg.get("role") == "user" and isinstance(msg.get("content"), list):
            # Anthropic format: content is list of blocks
            new_blocks = []
            for j, block in enumerate(msg["content"]):
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_result"
                    and j in clear_set
                ):
                    old_len = len(str(block.get("content", "")))
                    new_blocks.append({**block, "content": TIME_BASED_MC_CLEARED_MESSAGE})
                    cleared_count += 1
                    estimated_chars_freed += max(
                        0, old_len - len(TIME_BASED_MC_CLEARED_MESSAGE)
                    )
                else:
                    new_blocks.append(block)
            new_messages.append({**msg, "content": new_blocks})

        elif msg.get("role") == "tool":
            # OpenAI format: single tool result message
            if 0 in clear_set:
                old_len = len(str(msg.get("content", "")))
                new_messages.append({**msg, "content": TIME_BASED_MC_CLEARED_MESSAGE})
                cleared_count += 1
                estimated_chars_freed += max(
                    0, old_len - len(TIME_BASED_MC_CLEARED_MESSAGE)
                )
            else:
                new_messages.append(msg)
        else:
            new_messages.append(msg)

    return MicrocompactResult(
        messages=new_messages,
        cleared_count=cleared_count,
        estimated_tokens_freed=estimated_chars_freed // 4,
    )


def _collect_compactable_tool_results(
    messages: list[dict[str, Any]],
) -> list[tuple[int, int, str]]:
    """Collect positions of compactable tool results.

    Two-pass approach:
    1. Identify which tool_use IDs belong to compactable tools
    2. Find their corresponding tool_result positions

    Returns:
        List of (message_index, block_index, tool_use_id) tuples.
        For OpenAI format (role="tool"), block_index is always 0.
    """
    # First pass: identify which tool_use IDs belong to compactable tools
    compactable_tool_ids: set[str] = set()

    for msg in messages:
        # Anthropic: tool_use blocks in assistant messages
        if msg.get("role") == "assistant" and isinstance(msg.get("content"), list):
            for block in msg["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tname = block.get("name", "")
                    if tname in COMPACTABLE_TOOLS:
                        compactable_tool_ids.add(block.get("id", ""))
        # OpenAI: tool_calls in assistant messages
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls", []):
                func = tc.get("function", {})
                tname = func.get("name", "")
                if tname in COMPACTABLE_TOOLS:
                    compactable_tool_ids.add(tc.get("id", ""))

    # Second pass: find tool result positions for compactable tools
    positions: list[tuple[int, int, str]] = []

    for i, msg in enumerate(messages):
        # Anthropic: tool_result blocks in user messages
        if msg.get("role") == "user" and isinstance(msg.get("content"), list):
            for j, block in enumerate(msg["content"]):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tid = block.get("tool_use_id", "")
                    if tid in compactable_tool_ids:
                        positions.append((i, j, tid))
        # OpenAI: tool messages
        if msg.get("role") == "tool":
            tid = msg.get("tool_call_id", "")
            if tid in compactable_tool_ids:
                positions.append((i, 0, tid))

    return positions
